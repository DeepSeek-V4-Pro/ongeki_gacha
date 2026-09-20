"""账户级养成事务，复用 GachaDatabase 的连接和锁。"""
from __future__ import annotations

import json
from datetime import date
from typing import Callable

from .gacha_core import max_detail_slots
from .growth_catalog import GrowthCatalog
from .growth_core import CURVE_VERSION, MAIN_CHARACTER_IDS, affection_level
from .growth_migration import change_item


class GrowthError(ValueError):
    pass


class GrowthService:
    def __init__(self, database, catalog: GrowthCatalog):
        self.db = database
        self.catalog = catalog

    def automatic_voice_event(self, qq_id, request_id, *, claim=False):
        """只使用已提交交易；发送前持久化占位，重启及重投也不补发。"""
        if not request_id:return None
        key=json.dumps(['growth',qq_id,request_id],separators=(',',':'))
        voice_key=json.dumps(['automatic_voice',qq_id,request_id],separators=(',',':'))
        with self.db._lock:
            conn=self.db._conn
            if conn is None or not self.db._growth_enabled:return None
            conn.execute('BEGIN IMMEDIATE' if claim else 'BEGIN')
            try:
                row=conn.execute('SELECT source,result_json FROM growth_events WHERE event_key=? AND qq_id=?',(key,qq_id)).fetchone()
                if row is None or row[0] not in {'gift','companion'}:return None
                result=json.loads(row[1])
                if not result.get('success') or result.get('gain',0)<=0:return None
                kind='level_up' if result['level']>result['before_level'] else (
                    'gift_small' if result.get('gift_size')=='small' else 'gift_large') if row[0]=='gift' else None
                if kind is None or result['character_id'] not in MAIN_CHARACTER_IDS:return None
                event={'character_id':result['character_id'],'event':kind}
                if conn.execute('SELECT 1 FROM growth_events WHERE event_key=?',(voice_key,)).fetchone():return None
                if claim:
                    conn.execute('INSERT INTO growth_events VALUES(?,?,?,?,?,?,?)',
                        (voice_key,qq_id,'automatic_voice',json.dumps(event),json.dumps({'state':'attempt_reserved'}),CURVE_VERSION,self.db._now_iso()))
                    conn.commit()
                return event
            finally:
                if conn.in_transaction:conn.rollback()

    def _character(self, conn, qq_id, cid, now):
        if cid not in MAIN_CHARACTER_IDS:
            raise GrowthError("该角色不开放好感养成")
        conn.execute("""INSERT OR IGNORE INTO player_characters
            (qq_id,character_id,affection_points,curve_version,created_at,updated_at) VALUES(?,?,0,?,?,?)""",
            (qq_id, cid, CURVE_VERSION, now, now))
        row = conn.execute("SELECT affection_points,curve_version FROM player_characters WHERE qq_id=? AND character_id=?", (qq_id, cid)).fetchone()
        if row[1] != CURVE_VERSION:
            raise GrowthError("好感曲线版本不一致，需迁移")
        return int(row[0])

    def _rewards(self, conn, qq_id, cid, points, now):
        unlocked = []
        for reward in self.catalog.characters[cid]["rewards"]:
            if points < self.catalog.thresholds[int(reward["level"])]:
                continue
            changed = conn.execute("""INSERT OR IGNORE INTO affection_reward_claims
                VALUES(?,?,?,?,?)""", (qq_id, cid, reward["reward_key"], CURVE_VERSION, now))
            if not changed.rowcount:
                continue
            if reward["kind"] == "NormalCard":
                card = self.catalog.cards.by_id[int(reward["id"])]
                self.db._grant_card(conn, qq_id, card.id, card.rarity, now, source="affection_reward")
            else:
                conn.execute("INSERT OR IGNORE INTO player_cosmetics VALUES(?,?,?,?,?)",
                             (qq_id, reward["kind"], str(reward["id"]), reward["reward_key"], now))
                # 新获得的称号/装饰默认直接装备；同类型更高节点会覆盖为最新一件。
                column = {"Trophy": "title_id", "Attachment": "attachment_id"}.get(reward["kind"])
                if column:
                    conn.execute("INSERT OR IGNORE INTO player_growth_profile(qq_id) VALUES(?)", (qq_id,))
                    conn.execute(f"UPDATE player_growth_profile SET {column}=? WHERE qq_id=?",
                                 (str(reward["id"]), qq_id))
            unlocked.append(reward)
        return unlocked

    def _run(self, qq_id: str, request_id: str, action: str, payload: dict, operation: Callable) -> dict:
        if not request_id:
            return {"success": False, "error": "缺少消息ID，无法安全执行养成交易"}
        key = json.dumps(["growth", qq_id, request_id], separators=(",", ":"))
        request = json.dumps({"action": action, **payload}, sort_keys=True, ensure_ascii=False)
        with self.db._lock:
            conn = self.db._conn
            if conn is None:
                raise RuntimeError("数据库尚未打开")
            conn.execute("BEGIN IMMEDIATE")
            try:
                old = conn.execute("SELECT request_json,result_json FROM growth_events WHERE event_key=?", (key,)).fetchone()
                if old is not None:
                    if old[0] != request:
                        raise GrowthError("消息ID已用于另一笔养成交易")
                    conn.rollback()
                    return json.loads(old[1])
                if not self.db._growth_enabled:
                    raise GrowthError("养成功能当前暂停")
                self.db._ensure_player(conn, qq_id)
                now = self.db._now_iso()
                result = {"success": True, **operation(conn, now)}
                conn.execute("INSERT INTO growth_events VALUES(?,?,?,?,?,?,?)",
                             (key, qq_id, action, request, json.dumps(result, ensure_ascii=False), CURVE_VERSION, now))
                conn.commit()
                return result
            except GrowthError as exc:
                conn.rollback()
                return {"success": False, "error": str(exc)}
            except BaseException:
                conn.rollback()
                raise

    def partner(self, qq_id: str, cid: int, request_id: str) -> dict:
        def execute(conn, now):
            self._character(conn, qq_id, cid, now)
            conn.execute("""INSERT INTO player_growth_profile(qq_id,partner_character_id) VALUES(?,?)
                ON CONFLICT(qq_id) DO UPDATE SET partner_character_id=excluded.partner_character_id""", (qq_id, cid))
            return {"character_id": cid}
        return self._run(qq_id, request_id, "partner", {"character_id": cid}, execute)

    def ensure_starter_card(self, qq_id: str, cid: int) -> dict:
        """查看角色好感页时获取该角色第一张基础N卡；不占用养成交易消息ID。"""
        if cid not in MAIN_CHARACTER_IDS:
            raise GrowthError("该角色不开放好感养成")
        keys = tuple(r['reward_key'] for r in self.catalog.characters[cid]['rewards']
                     if r['kind'] == 'NormalCard')
        return self.db.grant_starter_card(qq_id, cid, reward_keys=keys)

    def accompany(self, qq_id: str, request_id: str) -> dict:
        def execute(conn, now):
            today = self.db.current_date_str(0)
            row = conn.execute("SELECT partner_character_id FROM player_growth_profile WHERE qq_id=?", (qq_id,)).fetchone()
            if row is None or row[0] is None:
                raise GrowthError("请先使用 /伙伴 <角色姓名> 选择伙伴")
            cid = row[0]
            points = self._character(conn, qq_id, cid, now)
            used = conn.execute("SELECT quantity FROM growth_daily_usage WHERE qq_id=? AND utc_date=? AND action='companion'", (qq_id, today)).fetchone()
            if used and used[0]:
                raise GrowthError("今天已陪伴，换伙伴不会重置每日额度")
            gain = self.catalog.rules["companion_points"]
            conn.execute("INSERT INTO growth_daily_usage VALUES(?,?,'companion',1)", (qq_id, today))
            return self._add_points(conn, qq_id, cid, points, gain, now)
        return self._run(qq_id, request_id, "companion", {}, execute)

    def _add_points(self, conn, qq_id, cid, points, gain, now):
        conn.execute("UPDATE player_characters SET affection_points=?,updated_at=? WHERE qq_id=? AND character_id=?",
                     (points + gain, now, qq_id, cid))
        rewards = self._rewards(conn, qq_id, cid, points + gain, now)
        return {"character_id": cid, "before_points": points, "points": points + gain,
                "gain": gain, "before_level": affection_level(points, self.catalog.thresholds),
                "level": affection_level(points + gain, self.catalog.thresholds), "rewards": rewards}

    def gift(self, qq_id: str, cid: int, size: str, quantity: int, request_id: str) -> dict:
        def execute(conn, now):
            if size not in self.catalog.rules["gift_points"] or type(quantity) is not int or not 1 <= quantity <= 1000000:
                raise GrowthError("礼物类型/数量无效（/送礼 <角色> 小 1）")
            points = self._character(conn, qq_id, cid, now)
            row = conn.execute("SELECT quantity FROM player_items WHERE qq_id=? AND item_id=?", (qq_id, f"gift_{size}")).fetchone()
            owned = row[0] if row else 0
            if owned < quantity:
                raise GrowthError(
                    f"礼物不足 {owned}/{quantity}（/礼物 购买 小 1）"
                )
            unit = self.catalog.rules["gift_points"][size]
            change_item(conn, qq_id, f"gift_{size}", -quantity)
            return {**self._add_points(conn, qq_id, cid, points, quantity * unit, now),
                    "gift_size": size, "requested": quantity, "consumed": quantity,
                    "returned": 0, "overflow_points": 0}
        return self._run(qq_id, request_id, "gift", {"character_id": cid, "size": size, "quantity": quantity}, execute)

    @staticmethod
    def _week_key(today: str) -> str:
        """按UTC日期所属ISO周生成限额周期键，跨年周不会串号。"""
        iso = date.fromisoformat(today).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def buy_gift(self, qq_id: str, size: str, quantity: int, request_id: str) -> dict:
        """用点数购买小/中礼物，每周限额按UTC ISO周结算。"""
        def execute(conn, now):
            plan = self.catalog.rules.get("gift_purchase", {}).get(size)
            if plan is None:
                raise GrowthError("可用点数购买的只有小礼物和中礼物")
            if type(quantity) is not int or not 1 <= quantity <= 100:
                raise GrowthError("购买数量需 1-100 份")
            label = {"small": "小礼物", "medium": "中礼物"}[size]
            week = self._week_key(self.db.current_date_str(0))
            action = f"gift_purchase_{size}"
            row = conn.execute("SELECT quantity FROM growth_daily_usage WHERE qq_id=? AND utc_date=? AND action=?",
                               (qq_id, week, action)).fetchone()
            used = int(row[0]) if row else 0
            if used + quantity > plan["weekly_cap"]:
                raise GrowthError(f"本周{label}已购满 {used}/{plan['weekly_cap']}")
            price = plan["price"] * quantity
            row = conn.execute("SELECT points FROM players WHERE qq_id=?", (qq_id,)).fetchone()
            owned = int(row[0]) if row else 0
            if owned < price:
                raise GrowthError(
                    f"点数不足 {owned}/{price}（/签到 获取点数）"
                )
            conn.execute("UPDATE players SET points=points-?, updated_at=? WHERE qq_id=?", (price, now, qq_id))
            change_item(conn, qq_id, f"gift_{size}", quantity)
            conn.execute("""INSERT INTO growth_daily_usage VALUES(?,?,?,?) ON CONFLICT(qq_id,utc_date,action)
                DO UPDATE SET quantity=excluded.quantity""", (qq_id, week, action, used + quantity))
            return {"size": size, "label": label, "quantity": quantity, "price": price,
                    "points": owned - price, "weekly_used": used + quantity,
                    "weekly_cap": plan["weekly_cap"]}

        return self._run(qq_id, request_id, "gift_purchase",
                         {"size": size, "quantity": quantity}, execute)

    def bloom(self, qq_id: str, card_id: int, stage: int, request_id: str) -> dict:
        def execute(conn, now):
            card = self.catalog.cards.by_id.get(card_id)
            if card is None or stage not in (1, 2):
                raise GrowthError("卡ID或阶段无效（/解花 <ID>）")
            row = conn.execute("SELECT copies,bloom_stage,kaika_at,cho_kaika_at,growth_origin FROM inventory WHERE qq_id=? AND card_id=?", (qq_id, card_id)).fetchone()
            if row is None or row[0] <= 0:
                raise GrowthError("未持有该卡（/卡册 角色 1 查卡ID）")
            if row[1] >= stage:
                return {"card_id": card_id, "stage": row[1], "spent": 0, "already_completed": True,
                        "completed_at": row[2 if stage == 1 else 3], "origin": row[4]}
            policy = self.catalog.mapping[card_id]["bloom_policy"]
            if policy == "blocked_unmapped":
                raise GrowthError("该卡暂缺角色映射，不能解花")
            if stage == 2 and row[1] != 1:
                raise GrowthError("请先 /解花 <卡ID>")
            if stage == 2:
                maximum = max_detail_slots(card.rarity)
                if row[0] < maximum:
                    raise GrowthError(
                        f"超解花需满星 {row[0]}/{maximum}（重复卡自动累计）"
                    )
            if policy == "main_affection":
                points = self._character(conn, qq_id, card.character_id, now)
                needed_level = self.catalog.rules["bloom_levels"][stage-1]
                needed = self.catalog.thresholds[needed_level]
                if points < needed:
                    raise GrowthError(
                        f"角色好感不足 Lv"
                        f"{affection_level(points, self.catalog.thresholds)}"
                        f"/{needed_level}（差 {needed-points} 点，/送礼 提升）"
                    )
            cost = self.catalog.rules["bloom_costs"][stage-1]
            item_id = str(self.catalog.rules["bloom_items"][stage-1])
            item_label = {
                "flower_fragment": "花之碎片",
                "bloom_ticket": "解花券",
                "gift_small": "小礼物",
                "gift_medium": "中礼物",
                "gift_large": "大礼物",
            }.get(item_id, item_id)
            item = conn.execute(
                "SELECT quantity FROM player_items WHERE qq_id=? AND item_id=?",
                (qq_id, item_id),
            ).fetchone()
            owned = item[0] if item else 0
            if owned < cost:
                raise GrowthError(
                    f"{item_label}不足 {owned}/{cost}（获取方式以 /规则 当前说明为准）"
                )
            change_item(conn, qq_id, item_id, -cost)
            conn.execute("""UPDATE inventory SET bloom_stage=?,is_kaika=1,is_cho_kaika=?,
                kaika_at=CASE WHEN ?=1 THEN ? ELSE kaika_at END,
                cho_kaika_at=CASE WHEN ?=2 THEN ? ELSE cho_kaika_at END WHERE qq_id=? AND card_id=?""",
                (stage, int(stage == 2), stage, now, stage, now, qq_id, card_id))
            return {
                "card_id": card_id,
                "stage": stage,
                "spent": cost,
                "item_id": item_id,
                "item_label": item_label,
                "remaining": owned - cost,
                "completed_at": now,
            }
        return self._run(qq_id, request_id, "bloom", {"card_id": card_id, "stage": stage}, execute)

    def equip(self, qq_id: str, kind: str, cosmetic_id: str, request_id: str) -> dict:
        def execute(conn, now):
            columns = {"Trophy": "title_id", "NamePlate": "nameplate_id", "Attachment": "attachment_id"}
            if kind not in columns:
                raise GrowthError("装扮类型必须为称号、名牌或装饰")
            if cosmetic_id != '卸下' and conn.execute("SELECT 1 FROM player_cosmetics WHERE qq_id=? AND cosmetic_type=? AND cosmetic_id=?", (qq_id, kind, cosmetic_id)).fetchone() is None:
                raise GrowthError("未解锁该装扮（/装扮 查看已解锁）")
            conn.execute("INSERT OR IGNORE INTO player_growth_profile(qq_id) VALUES(?)", (qq_id,))
            conn.execute(f"UPDATE player_growth_profile SET {columns[kind]}=? WHERE qq_id=?", (None if cosmetic_id == '卸下' else cosmetic_id, qq_id))
            return {"cosmetic_type": kind, "cosmetic_id": cosmetic_id}
        return self._run(qq_id, request_id, "equip", {"kind": kind, "id": cosmetic_id}, execute)

    def snapshot(self, qq_id: str) -> dict:
        with self.db._lock:
            conn = self.db._conn
            if conn is None:
                raise RuntimeError("数据库尚未打开")
            conn.execute("BEGIN IMMEDIATE")
            try:
                self.db._ensure_player(conn, qq_id)
                def rows(table):
                    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE qq_id=?", (qq_id,))]
                result = {name: rows(name) for name in ("player_characters", "player_growth_profile", "player_items",
                          "affection_reward_claims", "player_cosmetics", "inventory")}
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise

    def gift_purchase_state(self, qq_id: str) -> dict:
        """本周小/中礼物的点数购买额度，供背包页与回执显示。"""
        plans = self.catalog.rules.get("gift_purchase", {})
        week = self._week_key(self.db.current_date_str(0))
        state: dict = {}
        with self.db._lock:
            conn = self.db._conn
            if conn is None:
                raise RuntimeError("数据库尚未打开")
            for size, plan in plans.items():
                row = conn.execute(
                    "SELECT quantity FROM growth_daily_usage WHERE qq_id=? AND utc_date=? AND action=?",
                    (qq_id, week, f"gift_purchase_{size}")).fetchone()
                used = int(row[0]) if row else 0
                state[size] = {"price": plan["price"], "cap": plan["weekly_cap"],
                               "used": used, "left": plan["weekly_cap"] - used}
        return state
