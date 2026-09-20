"""养成命令与文本回执；交易提交后才执行渲染/发送。"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
import re
from time import time_ns
from maibot_sdk import Command

from .character_names import arguments
from .bloom_render import render_bloom_result
from .gacha_core import max_detail_slots
from .growth_core import (
    MAIN_CHARACTER_IDS,
    MAX_AFFECTION_LEVEL,
    REWARD_MAX_LEVEL,
    affection_level,
    affection_progress,
)
from .growth_render import render_affection, render_gift_inventory
from .profile_render import render_reward_pages


def request_identity(kwargs: dict, stream_id: str) -> str:
    message = kwargs.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    message_id = kwargs.get("message_id") or message.get("message_id")
    if message_id is None or str(message_id).strip() == "":
        return ""
    # 不同群可能具有相同平台消息号；账户每日额度仍跨群共用。
    return json.dumps([str(kwargs.get("platform") or message.get("platform") or "qq"), stream_id, str(message_id)], separators=(",", ":"))


def format_local_time(value: str, tz_offset_hours: int = 8) -> str:
    """把 ISO 时间按配置时区格式化为「YYYY-MM-DD HH:MM」，失败时原样返回。"""
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return str(value or "")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone(timedelta(hours=tz_offset_hours)))
    return local.strftime("%Y-%m-%d %H:%M")


def describe_receipt(
    action: str,
    result: dict,
    catalog,
    tz_offset_hours: int = 8,
) -> str:
    if not result["success"]:
        return result["error"]
    if action == "伙伴":
        cid = result["character_id"]
        name = catalog.characters[cid]['name']
        return f"当前伙伴：{name}\n/陪伴 涨好感｜/好感 {name} 看进度"
    if action in {"陪伴", "送礼"}:
        display = catalog.characters[result['character_id']]['name']
        lines = [
            f"{display} 好感 +{result['gain']}（累计 {result['points']}）",
            f"Lv{result['before_level']} → Lv{result['level']}",
        ]
        if "consumed" in result:
            lines.append(f"消耗礼物 ×{result['consumed']}")
        for reward in result["rewards"]:
            lines.append(f"解锁 Lv{reward['level']} {reward['name']}")
        return "\n".join(lines)
    if action in {"解花", "超解花"}:
        label = "超解花" if result["stage"] == 2 else "解花"
        card = catalog.cards.by_id.get(int(result.get("card_id") or 0))
        title = f"卡 {result['card_id']}"
        if card is not None:
            display = re.sub(r"^【[^】]+】\s*", "", card.name).strip() or card.name
            if len(display) > 20:
                display = display[:19] + "…"
            title = f"{display}（ID {result['card_id']}）"
        completed = format_local_time(
            result.get("completed_at") or "", tz_offset_hours
        )
        if result.get("already_completed"):
            return f"{title} 已完成{label}｜{completed or '旧版本继承'}"
        lines = [
            f"{label}成功：{title}",
            f"{result.get('item_label','解花券')} -{result['spent']}"
            f"（余 {result.get('remaining',0)}）｜完成 {completed}",
        ]
        if result["stage"] == 1:
            lines.append(f"/超解花 {result['card_id']} 继续超解花")
        return "\n".join(lines)
    if action == "礼物购买":
        size = '小' if result['size'] == 'small' else '中'
        return (
            f"已购买 {result['label']} ×{result['quantity']}"
            f"｜-{result['price']} 点（余 {result['points']}）"
            f"\n本周剩 {result['weekly_cap'] - result['weekly_used']} 份"
            f"｜/送礼 <角色姓名> {size} 1"
        )
    kind={'Trophy':'称号','NamePlate':'名牌','Attachment':'装饰'}.get(result['cosmetic_type'],result['cosmetic_type'])
    if result['cosmetic_id']=='卸下':
        return f"已卸下{kind}｜/装扮 查看已解锁"
    return f"已装备{kind} {result['cosmetic_id']}｜/好感 查看好感页"


class GrowthCommandsMixin:
    async def _send_bloom_result_image(
        self,
        stream_id: str,
        user: str,
        result: dict,
        catalog,
    ) -> bool:
        """解花/超解花成功后发送阶段对照图；失败回退纯文字。"""
        renderer = getattr(self, "_renderer", None)
        service = getattr(self, "_growth", None)
        if renderer is None or service is None or not getattr(self, "_render_ready", False):
            return False
        card = catalog.cards.by_id.get(int(result.get("card_id") or 0))
        if card is None:
            return False
        try:
            snapshot = await asyncio.to_thread(service.snapshot, user)
            copies = next(
                (
                    int(row["copies"])
                    for row in snapshot["inventory"]
                    if row["card_id"] == card.id
                ),
                0,
            )
            output = (
                self.ctx.paths.runtime_dir
                / f"growth_bloom_{card.id}_{time_ns()}.png"
            )
            path = await asyncio.to_thread(
                render_bloom_result,
                renderer,
                card,
                copies,
                int(result["stage"]),
                output,
                spent=int(result.get("spent") or 0),
                remaining=int(result.get("remaining") or 0),
                item_label=str(result.get("item_label") or "解花券"),
            )
            image_base64 = base64.b64encode(path.read_bytes()).decode("ascii")
            sent = await self.ctx.send.image(image_base64, stream_id)
            return sent is True or (
                isinstance(sent, dict)
                and (sent.get("sent") is True or sent.get("success") is True)
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("解花结果图发送失败，回退文字回执: %s", exc)
            return False

    @Command("ongeki_character_voice", description="角色语音目录与点播",
             pattern=r"^/角色语音(?:\s+(?P<voice_args>.*))?\s*$")
    async def handle_character_voice(self, stream_id: str = "", **kwargs):
        if self._voice is None or self._growth is None:
            text="角色语音服务尚未初始化"
        else:
            raw=(kwargs.get('matched_groups') or {}).get('voice_args')
            user=self._user_id(kwargs)
            try:
                args=arguments(self._growth.catalog,'角色语音',raw)
                if user=='unknown':raise ValueError('无法识别账号')
                if args==['分类']:
                    text='【自动回应】小礼物/中·大礼物/好感升级\n'
                    text+='【手动播放】每角色 10 条档案语音（共用冷却）\n'
                    text+='获得好感自动发｜列表 /角色语音 星咲 あかり'
                elif len(args)==2:
                    result=await self._voice.play(user,int(args[0]),int(args[1]),request_identity(kwargs,stream_id),stream_id)
                    if result['success']:return True,'角色语音已发送',True
                    text=result['error']
                else:
                    if len(args)>1:raise ValueError('用法：/角色语音 <角色姓名> [序号]')
                    snapshot=await asyncio.to_thread(self._growth.snapshot,user)
                    profile=snapshot['player_growth_profile']
                    cid=int(args[0]) if args else profile[0]['partner_character_id'] if profile else None
                    if cid not in MAIN_CHARACTER_IDS:raise ValueError('请指定主角色姓名或先选择伙伴')
                    claimed={r['reward_key'] for r in snapshot['affection_reward_claims'] if r['character_id']==cid}
                    display=self._growth.catalog.characters[cid]['name']
                    lines=[f"{display} · 角色语音",
                        '【自动回应】小礼物、中/大礼物、好感升级（获得好感后自动发送，无需手动点播）',
                        '【手动播放】档案语音（达到等级解锁后按序号点播）']
                    for number in range(1,11):
                        row=self._voice.catalog.voices[(cid,number)]
                        if row['reward_key'] not in claimed:
                            status='未解锁'
                        elif row.get('listening_review')!='verified':
                            status='素材校验未通过'
                        else:
                            status='已解锁'
                        lines.append(f"{number:02d} · Lv{row['unlock_level']} · {status}")
                        if status=='已解锁':
                            lines.append(f"/角色语音 {display} {number}")
                    text='\n'.join(lines)
            except (ValueError,KeyError) as exc:
                text=str(exc)
        await self._send_text(stream_id,text,title='角色语音')
        return True,text,True

    @Command("ongeki_growth", description="主角色好感与卡牌养成",
             pattern=r"^/(?P<growth_action>好感奖励|超解花|伙伴|好感|陪伴|礼物|送礼|养成|解花|装扮)(?:\s+(?P<growth_args>.*))?\s*$")
    async def handle_growth(self, stream_id: str = "", **kwargs):
        service = self._growth
        if service is None:
            text = "养成服务未就绪"
            await self._send_text(stream_id, text)
            return True, text, True
        user = self._user_id(kwargs)
        if user == "unknown":
            text = "无法识别账号，未执行操作"
            await self._send_text(stream_id, text)
            return True, text, True
        groups = kwargs.get("matched_groups") or {}
        action = str(groups.get("growth_action") or "好感")
        raw_args = str(groups.get("growth_args") or "")
        args = raw_args.split()
        request_id = request_identity(kwargs, stream_id)
        catalog = service.catalog
        snapshot = None
        portrait_card = None
        purchase = None
        starter_reveal = None
        starter_card = None
        try:
            result = None
            args=arguments(catalog,action,raw_args)
            if action == "好感" and args == ["列表"]:
                action, args = "列表", []
            if action == "伙伴" and len(args) == 1:
                result = await asyncio.to_thread(service.partner, user, int(args[0]), request_id)
            elif action == "陪伴" and not args:
                result = await asyncio.to_thread(service.accompany, user, request_id)
            elif action == "送礼" and len(args) in (2, 3):
                size = {"小": "small", "中": "medium", "大": "large"}.get(args[1], "")
                result = await asyncio.to_thread(service.gift, user, int(args[0]), size, int(args[2]) if len(args) == 3 else 1, request_id)
            elif action in {"解花", "超解花"} and len(args) == 1:
                result = await asyncio.to_thread(service.bloom, user, int(args[0]), 1 if action == "解花" else 2, request_id)
            elif action == '装扮' and not args:
                snapshot=await asyncio.to_thread(service.snapshot,user)
                owned={(r['cosmetic_type'],str(r['cosmetic_id'])) for r in snapshot['player_cosmetics']}
                reward_catalog=getattr(self,'_reward_catalog',None)
                lines=['已解锁的好感页装扮']
                for r in reward_catalog.by_key.values() if reward_catalog else []:
                    label={'Trophy':'称号','Attachment':'装饰'}.get(r['kind'])
                    if label and (r['kind'],str(r['id'])) in owned:
                        lines.extend([f"{r['name']}（ID {r['id']}）",f"/装扮 {label} {r['id']}"])
                if len(lines)==1:lines.append('尚无装扮，提升角色好感即可解锁')
                lines.extend(['/装扮 称号 卸下','/装扮 装饰 卸下','/好感'])
                text='\n'.join(lines)
            elif action == "装扮" and len(args) == 2:
                kind = {"称号": "Trophy", "名牌": "NamePlate", "装饰": "Attachment"}.get(args[0], "")
                if kind == 'NamePlate' and args[1] != '卸下':
                    text = '名牌展示已停用，已解锁名牌保留为收藏。可使用 /装扮 查看称号和装饰。'
                else:
                    result = await asyncio.to_thread(service.equip, user, kind, args[1], request_id)
            elif action == "礼物" and args and args[0] in {"购买", "买"}:
                if len(args) not in (2, 3):
                    raise ValueError("用法：/礼物 购买 小/中 [数量]")
                size = {"小": "small", "中": "medium"}.get(args[1], "")
                result = await asyncio.to_thread(
                    service.buy_gift, user, size, int(args[2]) if len(args) == 3 else 1, request_id)
                action = "礼物购买"
            elif action in {"好感", "好感奖励", "礼物", "养成", "列表"}:
                snapshot = await asyncio.to_thread(service.snapshot, user)
                if action == "好感":
                    target_cid = int(args[0]) if args else None
                    if target_cid is None and snapshot["player_growth_profile"]:
                        target_cid = snapshot["player_growth_profile"][0].get("partner_character_id")
                    if target_cid in MAIN_CHARACTER_IDS:
                        starter_reveal = await asyncio.to_thread(
                            service.ensure_starter_card, user, int(target_cid)
                        )
                        if starter_reveal.get("granted"):
                            snapshot = await asyncio.to_thread(service.snapshot, user)
                            starter_card = catalog.cards.by_id.get(int(starter_reveal["card_id"]))
                if action == "礼物":
                    purchase = await asyncio.to_thread(service.gift_purchase_state, user)
                if action == '好感' and len(args)==3 and args[1]=='卡面':
                    portrait_card=catalog.cards.by_id.get(int(args[2]))
                    if portrait_card is None or portrait_card.character_id!=int(args[0]):
                        raise ValueError('可选卡面必须属于指定角色')
                    if not any(r['card_id']==portrait_card.id and r['copies']>0 for r in snapshot['inventory']):
                        raise ValueError('尚未持有该展示卡面')
                    args=args[:1]
                text = self._growth_query(
                    action,
                    args,
                    snapshot,
                    catalog,
                    purchase,
                    self.config.economy.tz_offset_hours,
                )
            else:
                raise ValueError("命令参数数量无效")
            if result is not None:
                text = describe_receipt(
                    action,
                    result,
                    catalog,
                    tz_offset_hours=self.config.economy.tz_offset_hours,
                )
        except (ValueError, KeyError) as exc:
            text = (
                f"参数无效：{exc}\n"
                "例 /好感 星咲 あかり｜/伙伴 星咲 あかり｜/陪伴\n"
                "卡牌 /养成 <ID>｜/解花 <ID>｜更多 /帮助"
            )
        sent=False
        text_as_plain=False
        if snapshot is not None and not text.startswith('参数无效'):
            try:
                sent=await self._growth_image(stream_id,action,args,snapshot,catalog,portrait_card,purchase)
            except Exception as exc:
                self.ctx.logger.warning('养成图片失败，使用文本回执: %s',exc)
        if action == "礼物购买" and result is not None and result.get("success"):
            await self._send_item_gain_card(
                stream_id,
                user,
                {f"gift_{result['size']}": int(result["quantity"])},
                title="购买获得物品",
                subtitle=(
                    f"{result['label']} ×{result['quantity']}"
                    f"｜-{result['price']} 点｜剩余 {result['points']} 点"
                ),
            )
        if (
            action in {"解花", "超解花"}
            and result is not None
            and result.get("success")
            and not result.get("already_completed")
        ):
            await self._send_bloom_result_image(stream_id, user, result, catalog)
        if not sent:
            text_as_plain=bool(
                await self._send_text(stream_id, text, title="角色养成")
            )
        if result is not None and result.get('success') and self._voice is not None:
            try:
                await self._voice.automatic(user,request_id,stream_id)
            except Exception as exc:
                self.ctx.logger.warning('养成已完成，自动语音失败: %s',exc)
        # 只有整条回复是图片（卡片图/长文本图）时才补发可复制的指令行，
        # 否则正文里已经有这些命令，再发一遍会造成同一内容出现两次。
        commands = [line for line in text.splitlines() if line.startswith("/")]
        if commands and not text_as_plain:
            await self.ctx.send.text("\n".join(commands), stream_id)
        if starter_reveal is not None and starter_reveal.get("granted") and starter_card is not None:
            await self._send_card_reveal(
                stream_id,
                starter_card,
                int(starter_reveal.get("copies") or 1),
                mode="new",
            )
        try:
            await self._send_pending_card_reveals(stream_id, user)
        except Exception as exc:
            self.ctx.logger.warning('待发卡牌揭示发送失败: %s', exc)
        return True, text, True

    async def _growth_image(self, stream_id, action, args, snapshot, catalog, portrait_card, purchase=None):
        if not getattr(self, "_render_ready", False):
            # 未接入字体等素材时直接走文字回执，不做无意义的渲染尝试。
            return False
        output=self.ctx.paths.runtime_dir/f'growth_{time_ns()}.png'
        if action=='礼物' and not args:
            items={r['item_id']:r['quantity'] for r in snapshot['player_items']}
            path=await asyncio.to_thread(render_gift_inventory,items,output,purchase)
        elif action=='好感':
            profile=snapshot['player_growth_profile']
            partner=profile[0]['partner_character_id'] if profile else None
            cid=int(args[0]) if args else partner
            if cid not in MAIN_CHARACTER_IDS:return False
            points=next((r['affection_points'] for r in snapshot['player_characters'] if r['character_id']==cid),0)
            portrait=await asyncio.to_thread(self._renderer._load_card,portrait_card) if portrait_card else None
            path=await asyncio.to_thread(render_affection,catalog,cid,points,portrait,output,
                                         partner=cid==partner,portrait_mode='card' if portrait_card else 'original',
                                         snapshot=snapshot,rewards=list(self._reward_catalog.by_key.values()))
        elif action=='好感奖励':
            profile=snapshot['player_growth_profile']
            cid=int(args[0]) if args else profile[0]['partner_character_id'] if profile else None
            if cid not in MAIN_CHARACTER_IDS:return False
            paths=await asyncio.to_thread(render_reward_pages,catalog,self._reward_catalog.rewards(cid),cid,snapshot,output)
            for path in paths:
                result=await self.ctx.send.image(base64.b64encode(path.read_bytes()).decode('ascii'),stream_id)
                if not (result is True or isinstance(result,dict) and (result.get('sent') is True or result.get('success') is True)):return False
            return True
        else:
            return False
        result=await self.ctx.send.image(base64.b64encode(path.read_bytes()).decode('ascii'),stream_id)
        success=result is True or isinstance(result,dict) and (result.get('sent') is True or result.get('success') is True)
        return bool(success)

    @staticmethod
    def _growth_query(action, args, snapshot, catalog, purchase=None, tz_offset_hours=8):
        points = {r["character_id"]: r["affection_points"] for r in snapshot["player_characters"]}
        inventory = {r["card_id"]: r for r in snapshot["inventory"]}
        owned_characters = {catalog.cards.by_id[cid].character_id for cid, r in inventory.items()
                            if cid in catalog.cards.by_id and r["copies"] > 0}
        profile = snapshot["player_growth_profile"][0] if snapshot["player_growth_profile"] else {}
        partner = profile.get("partner_character_id")
        items = {r["item_id"]: r["quantity"] for r in snapshot["player_items"]}
        if action == "礼物":
            if args:
                raise ValueError("/礼物 不需要参数")
            lines = [
                f"小 {items.get('gift_small',0)}｜中 {items.get('gift_medium',0)}"
                f"｜大 {items.get('gift_large',0)}"
                f"｜碎片 {items.get('flower_fragment',0)}"
                f"｜解花券 {items.get('bloom_ticket',0)}",
            ]
            for size, plan in (purchase or {}).items():
                label = {"small": "小礼物", "medium": "中礼物"}[size]
                lines.append(
                    f"{label} {plan['price']} 点/份"
                    f"（本周可买 {plan['left']}/{plan['cap']}）"
                )
            lines.append("/礼物 购买 小 1")
            return "\n".join(lines)
        if action == "列表":
            return "\n".join(f"{c['name']}｜" +
                f"Lv{affection_level(points.get(cid,0), catalog.thresholds)}" +
                ("｜伙伴" if cid == partner else "") for cid,c in catalog.characters.items()) + "\n/好感 星咲 あかり"
        if action == "养成":
            if len(args) != 1:
                raise ValueError("需要卡ID")
            cid = int(args[0])
            card = catalog.cards.by_id.get(cid)
            if card is None:
                return "卡ID不存在｜可用 /卡册 <角色姓名> 1 查看已拥有的卡ID"
            row = inventory.get(cid, {})
            stage = row.get("bloom_stage", 0)
            current = row.get("copies", 0)
            mapping = catalog.mapping[cid]
            lines = [
                f"{(card.name[:21] + '…') if len(card.name) > 22 else card.name}"
                f"｜ID {cid}",
                f"{['未解花','解花','超解花'][stage]}"
                f"｜持有 {current}/{max_detail_slots(card.rarity)}"
                f"｜花之碎片 {items.get('flower_fragment',0)}"
                f"｜解花券 {items.get('bloom_ticket',0)}",
            ]
            if mapping["bloom_policy"] == "blocked_unmapped":
                lines.append("暂缺角色映射，新解花已阻断")
            elif stage < 2:
                maximum = max_detail_slots(card.rarity)
                if mapping["bloom_policy"] == "main_affection":
                    needed_level = catalog.rules["bloom_levels"][stage]
                    level = affection_level(points.get(card.character_id,0), catalog.thresholds)
                    if stage == 0:
                        lines.append(
                            f"解花需好感 Lv{needed_level}（现 Lv{level}）"
                        )
                    else:
                        lines.append(
                            f"超解花需已解花＋满星 {current}/{maximum}"
                            f"＋好感 Lv{needed_level}（现 Lv{level}）"
                        )
                elif stage == 0:
                    lines.append("解花需解花券（该角色无好感养成）")
                else:
                    lines.append(
                        f"超解花需已解花＋满星 {current}/{maximum}（该角色无好感养成）"
                    )
                item_id = str(catalog.rules["bloom_items"][stage])
                item_label = {
                    "bloom_ticket": "解花券",
                    "flower_fragment": "花之碎片",
                    "gift_small": "小礼物",
                    "gift_medium": "中礼物",
                    "gift_large": "大礼物",
                }.get(item_id, item_id)
                lines.append(f"消耗{item_label} {catalog.rules['bloom_costs'][stage]}")
                lines.append(f"/{'解花' if stage == 0 else '超解花'} {cid}")
            if stage:
                completed = row.get('cho_kaika_at') if stage == 2 else row.get('kaika_at')
                lines.append(
                    "完成 "
                    + (format_local_time(completed, tz_offset_hours)
                       if completed else "旧版本继承")
                )
            return "\n".join(lines)
        if len(args) > 1:
            raise ValueError("最多一个角色姓名")
        cid = int(args[0]) if args else partner
        if cid is None:
            return "请先选择伙伴或指定角色姓名\n/伙伴 星咲 あかり"
        if cid not in MAIN_CHARACTER_IDS:
            return "该角色不开放好感养成"
        character = catalog.characters[cid]
        count = points.get(cid, 0)
        level = affection_level(count, catalog.thresholds)
        lines = [f"{character['name']}", f"Lv{level}｜累计 {count} 点"]
        if action == "好感奖励":
            claimed = {r["reward_key"] for r in snapshot["affection_reward_claims"] if r["character_id"] == cid}
            lines.extend(f"Lv{r['level']} {r['name']}｜{'已领取' if r['reward_key'] in claimed else '未领取'}" for r in character["rewards"])
            return "\n".join(lines)
        _level, _ratio, current, need = affection_progress(count, catalog.thresholds)
        if level >= MAX_AFFECTION_LEVEL:
            lines.append("好感 99 / 99 已满（累计仍增加）")
        else:
            lines.append(f"本级 {current}/{need}｜距下级 {need-current} 点")
        next_reward = next((r for r in character["rewards"] if int(r["level"]) > level), None)
        if next_reward:
            lines.append(f"下一奖励：Lv{next_reward['level']} {next_reward['name']}，还差 {catalog.thresholds[int(next_reward['level'])]-count} 点")
        elif level >= REWARD_MAX_LEVEL:
            lines.append("奖励节点已全部达成，好感继续累计")
        display = character["name"]
        lines.extend([f"/伙伴 {display}", f"/送礼 {display} 小 1", f"/好感奖励 {display}"])
        return "\n".join(lines)
