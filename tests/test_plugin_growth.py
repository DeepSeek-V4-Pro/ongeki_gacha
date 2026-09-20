"""实际SDK组件发现、生命周期与命令入口；全部消息由本地Mock接收。"""
import logging
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ..growth_migration import change_item

try:
    from ..plugin import OngekiGachaPlugin
except ModuleNotFoundError as exc:
    if exc.name!='maibot_sdk':raise
    OngekiGachaPlugin=None


@unittest.skipIf(OngekiGachaPlugin is None,'需要实际 maibot_sdk 环境')
class PluginGrowthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.sender=SimpleNamespace(text=AsyncMock(return_value=True),image=AsyncMock(return_value={'success':True}),custom=AsyncMock(return_value=True))
        self.plugin=OngekiGachaPlugin()
        self.plugin._set_context(SimpleNamespace(paths=SimpleNamespace(data_dir=root,runtime_dir=root),send=self.sender,logger=logging.getLogger('growth-test')))
        config=self.plugin.build_default_config();config['growth']['enabled']=True;config['growth']['voice_enabled']=False;config['task']['enabled']=False;config['admin']['admin_ids']=['user']
        self.plugin.set_plugin_config(config)
        await self.plugin.on_load()

    async def asyncTearDown(self):
        await self.plugin.on_unload();self.temp.cleanup()

    async def dispatch(self,text,message='message'):
        for component in self.plugin.get_components():
            if component['type']!='COMMAND':continue
            metadata=component['metadata'];match=re.fullmatch(metadata['command_pattern'],text)
            if match:
                return await getattr(self.plugin,metadata['handler_name'])(stream_id='mock-group',user_id='user',message_id=message,matched_groups=match.groupdict())
        self.fail(f'命令未注册: {text}')

    async def test_registered_commands_execute_and_replay(self):
        card=next(c for c in self.plugin._cards.cards if c.character_id==1000)
        self.plugin._db.commit_draw('user',[(card.id,card.rarity)],cost=0)
        response=await self.dispatch('/伙伴 星咲明','partner')
        self.assertIn('当前伙伴',response[1])
        first=await self.dispatch('/陪伴','accompany')
        before=self.plugin._growth.snapshot('user')
        self.assertEqual(first,await self.dispatch('/陪伴','accompany'))
        self.assertEqual(before,self.plugin._growth.snapshot('user'))
        await self.dispatch('/好感 星咲 あかり','query')
        self.sender.image.assert_awaited()
        classification=(await self.dispatch('/角色语音 分类','classification'))[1]
        self.assertIn('自动回应',classification)
        self.assertIn('手动播放',classification)
        self.sender.custom.assert_not_called()

    async def test_short_reply_stays_text_and_long_reply_uses_card(self):
        self.sender.text.reset_mock();self.sender.image.reset_mock()
        await self.dispatch('/点数','points')
        self.sender.text.assert_awaited()
        self.sender.image.assert_not_awaited()
        self.sender.text.reset_mock();self.sender.image.reset_mock()
        await self.dispatch('/帮助','help')
        self.sender.image.assert_awaited()
        self.sender.text.assert_not_awaited()
        self.assertTrue(self.plugin._is_short_reply(['一行回执'],'一行回执'))
        self.assertFalse(self.plugin._is_short_reply(['第 %d 行' % i for i in range(11)],'短'))

    async def test_hot_reload_preserves_voice_limiter_and_unload_releases(self):
        voice=self.plugin._voice
        self.plugin._db.add_timed_event('user','voice_attempt',123.0)
        await self.plugin.on_config_update('self',{},'test')
        self.assertIs(self.plugin._voice,voice)
        self.assertEqual(
            [row[0] for row in self.plugin._db.list_timed_events('voice_attempt',qq_id='user')],
            [123.0],
        )
        database=self.plugin._db
        await self.plugin.on_unload()
        self.assertIsNone(database._conn)
        self.assertIsNone(self.plugin._growth)
        self.assertIsNone(self.plugin._voice)

    async def test_affection_entry_and_decorated_affection(self):
        from ..starter_cards import STARTER_CARDS
        self.assertTrue(self.plugin.build_default_config()['growth']['enabled'])
        await self.dispatch('/伙伴 柏木美亜','new-partner')
        snap=self.plugin._growth.snapshot('user')
        self.assertEqual(snap['inventory'],[])
        self.sender.image.reset_mock()
        response=await self.dispatch('/好感 柏木美亜','affection')
        self.assertNotIn('账号档案',response[1])
        snap=self.plugin._growth.snapshot('user')
        self.assertEqual({r['card_id'] for r in snap['inventory']},{STARTER_CARDS[1013]})
        self.assertEqual(self.sender.image.await_count,2)
        self.sender.image.reset_mock()
        await self.dispatch('/好感奖励 柏木美亜','rewards')
        self.assertEqual(self.sender.image.await_count,7)

    async def test_duplicate_entries_are_removed(self):
        patterns=[c['metadata']['command_pattern'] for c in self.plugin.get_components() if c['type']=='COMMAND']
        self.assertFalse([p for p in patterns if '档案' in p or '资料' in p])
        self.assertFalse([p for p in patterns if '播放语音' in p or '语音分类' in p])
        self.assertFalse([p for p in patterns if '|角色|' in p or '|图鉴|' in p])
        overview=await self.dispatch('/好感 列表','overview')
        self.assertIn('Lv',overview[1])
        self.sender.image.reset_mock()
        page=await self.dispatch('/好感 星咲明','affection-page')
        self.sender.image.assert_awaited()
        self.assertIn('星咲 あかり',page[1])
        played=await self.dispatch('/角色语音 星咲明 1','voice-play')
        self.assertTrue(played[0])

    async def test_gift_purchase_entry(self):
        await self.dispatch('/伙伴 星咲明','buy-partner')
        plan=self.plugin._growth.catalog.rules['gift_purchase']['small']
        self.plugin._db._conn.execute("UPDATE players SET points=? WHERE qq_id='user'",(plan['price'],))
        bought=await self.dispatch('/礼物 购买 小 1','buy-gift')
        self.assertIn('已购买',bought[1])
        self.assertIn('-'+str(plan['price'])+' 点',bought[1])
        self.assertEqual(self.plugin._db.get_player('user').points,0)
        items={r['item_id']:r['quantity'] for r in self.plugin._growth.snapshot('user')['player_items']}
        self.assertEqual(items['gift_small'],1)
        denied=await self.dispatch('/礼物 购买 小 1','buy-gift-2')
        self.assertIn('点数不足',denied[1])
        page=await self.dispatch('/礼物','gift-page')
        self.assertTrue(page[0])

    async def test_image_reply_does_not_repeat_the_same_commands(self):
        """图片已送达时只补发可复制的指令行，正文不再重复一遍。"""
        await self.dispatch('/伙伴 星咲明','dup-partner')
        self.sender.text.reset_mock();self.sender.image.reset_mock()
        await self.dispatch('/好感 星咲明','dup-affection')
        self.sender.image.assert_awaited()
        self.assertEqual(self.sender.text.await_count,1)
        hint=self.sender.text.await_args[0][0]
        self.assertTrue(hint.splitlines())
        self.assertTrue(all(line.startswith('/') for line in hint.splitlines()))

    async def test_text_reply_is_not_followed_by_a_second_copy(self):
        """图片发送失败时正文照常发，但不再额外补发同样的命令行。"""
        await self.dispatch('/伙伴 星咲明','dup-partner-2')
        self.sender.text.reset_mock();self.sender.image.reset_mock()
        self.sender.image.side_effect=RuntimeError('boom')
        try:
            await self.dispatch('/好感 星咲明','dup-affection-2')
        finally:
            self.sender.image.side_effect=None
        self.assertEqual(self.sender.text.await_count,1)
        self.assertIn('Lv',self.sender.text.await_args[0][0])

    async def test_pool_image_download_uses_configured_size_limit(self):
        from unittest.mock import MagicMock
        response=MagicMock()
        response.headers={'Content-Type':'image/png'}
        response.read.return_value=b'png-data'
        response.__enter__.return_value=response
        with patch('ongeki_gacha.plugin.urllib.request.urlopen',return_value=response):
            self.assertEqual(self.plugin._download_pool_image('https://example.test/a.png'),b'png-data')
        response.read.assert_called_once_with(self.plugin.config.ui.max_pool_image_bytes+1)

    async def test_pending_reveal_is_retained_until_send_confirmed(self):
        db=self.plugin._db
        card=next(c for c in self.plugin._cards.cards if c.rarity=='N')
        db._conn.execute("INSERT INTO pending_card_reveals(qq_id,card_id,before_copies,after_copies,created_at) VALUES('user',?,1,2,'now')",(card.id,))
        with patch.object(self.plugin,'_send_card_reveal',new=AsyncMock(return_value=False)):
            await self.plugin._send_pending_card_reveals('g','user')
        self.assertEqual(len(db.list_pending_card_reveals('user')),1)
        with patch.object(self.plugin,'_send_card_reveal',new=AsyncMock(return_value=True)):
            await self.plugin._send_pending_card_reveals('g','user')
        self.assertEqual(db.list_pending_card_reveals('user'),[])

    async def test_draw_message_replay_does_not_charge_twice(self):
        self.plugin._db.get_player('user')
        self.plugin._db._conn.execute("UPDATE players SET points=10000 WHERE qq_id='user'")
        with patch.object(self.plugin._renderer,'render',return_value=b'png'), patch.object(self.plugin,'_send_card_reveal',new=AsyncMock(return_value=True)):
            await self.dispatch('/抽卡 5','same-draw')
            points=self.plugin._db.get_player('user').points
            self.assertLess(points,10000)
            await self.dispatch('/抽卡 5','same-draw')
        self.assertEqual(self.plugin._db.get_player('user').points,points)
        self.assertEqual(self.plugin._db._conn.execute("SELECT COUNT(*) FROM gacha_logs WHERE qq_id='user'").fetchone()[0],1)

    async def test_bad_catalog_load_cleans_up_database(self):
        await self.plugin.on_unload()
        with patch('ongeki_gacha.plugin.GrowthCatalog',side_effect=ValueError('bad catalog')):
            with self.assertRaisesRegex(ValueError,'bad catalog'):await self.plugin.on_load()
        self.assertIsNone(self.plugin._db)
        self.assertIsNone(self.plugin._cleanup_task)

    async def test_task_review_growth_receipts(self):
        advanced=self.plugin._db.create_task('user',task_kind='advanced',game='maimai',song_id='s0',song_title='T0')
        self.assertTrue(advanced.success)
        self.assertTrue(self.plugin._db.submit_task(advanced.task_id,'user').success)
        response=await self.dispatch(f'/任务审核 {advanced.task_id} SSS','review-advanced')
        self.assertIn(
            f"已发放 {self.plugin.config.task.advanced_reward_sss} 点",
            response[1],
        )
        self.assertIn('中礼物 ×1',response[1])
        self.assertIn('花之碎片 +1',response[1])
        challenge=self.plugin._db.create_task('user',task_kind='challenge',game='maimai',song_id='s1',song_title='T1')
        self.assertTrue(challenge.success)
        self.assertTrue(self.plugin._db.submit_task(challenge.task_id,'user').success)
        response=await self.dispatch(f'/任务审核 {challenge.task_id} SSS','review')
        self.assertIn(
            f"已发放 {self.plugin.config.task.challenge_reward_sss} 点",
            response[1],
        )
        self.assertNotIn('中礼物',response[1])
        self.assertIn('花之碎片 +1',response[1])
        ultimate=self.plugin._db.create_task('12345',task_kind='ultimate',game='chunithm',song_id='s2',song_title='T2')
        self.assertTrue(ultimate.success)
        self.assertTrue(self.plugin._db.submit_task(ultimate.task_id,'12345').success)
        response=await self.dispatch(f'/终极完成 12345 {ultimate.task_id}','ultimate')
        self.assertIn('已发放 30000 点',response[1])
        self.assertNotIn('养成奖励',response[1])

    async def test_economic_defaults_match_local_config(self):
        import tomllib
        config_path=Path(__file__).parents[1]/'config.toml'
        if not config_path.is_file():
            self.skipTest('本地 config.toml 不入库，全新检出时跳过默认值对照')
        config=tomllib.loads(config_path.read_text(encoding='utf8'))
        defaults=self.plugin.build_default_config()
        for section in ('economy','task','monthly_card','growth','pool','ui'):
            for key,value in config[section].items():
                if section=='growth' and key in {'enabled','voice_enabled','automatic_voice_enabled'}:
                    continue
                self.assertEqual(defaults[section][key],value,f'{section}.{key}')
        self.assertEqual(defaults['task']['ultimate_reward'],30000)
        self.assertEqual(
            defaults['task']['normal_count']*defaults['task']['normal_reward']
            + defaults['task']['challenge_count']*defaults['task']['challenge_reward_sss']
            + defaults['task']['advanced_count']*defaults['task']['advanced_reward_sss'],
            430,
        )

    async def test_catalog_points_pool_and_item_images(self):
        await self.dispatch('/伙伴 星咲 あかり','catalog-prep')
        pool=await self.dispatch('/卡池 下一期','pool-next')
        self.assertIn('模拟档期',pool[1])
        self.assertIn('下一期：',pool[1])
        points=await self.dispatch('/点数','points-fragments')
        self.assertIn('花之碎片',points[1])
        self.assertIn('解花券',points[1])
        self.sender.image.reset_mock()
        index=await self.dispatch('/卡册','catalog-index')
        self.assertIn('卡册总览',index[1])
        self.sender.image.assert_awaited_once()
        self.sender.image.reset_mock()
        page=await self.dispatch('/卡册 星咲 あかり 1','catalog-page')
        self.assertIn('第 1/',page[1])
        self.sender.image.assert_awaited_once()
        other=await self.dispatch('/卡册 其他 1','catalog-other')
        self.assertIn('其他',other[1])
        plan=self.plugin._growth.catalog.rules['gift_purchase']['small']
        self.plugin._db._conn.execute(
            "UPDATE players SET points=? WHERE qq_id='user'",(plan['price'],)
        )
        self.sender.image.reset_mock()
        await self.dispatch('/礼物 购买 小 1','buy-item-image')
        self.sender.image.assert_awaited_once()

    async def test_bloom_command_sends_result_image(self):
        await self.dispatch('/伙伴 星咲 あかり','bloom-prep')
        card=next(c for c in self.plugin._cards.cards if c.character_id==1000 and c.rarity=='SSR')
        self.plugin._db.commit_draw('user',[(card.id,card.rarity)]*5,cost=0)
        conn=self.plugin._db._conn
        conn.execute('BEGIN IMMEDIATE')
        try:
            change_item(conn,'user','bloom_ticket',5)
            conn.execute(
                "UPDATE player_characters SET affection_points=? "
                "WHERE qq_id='user' AND character_id=1000",
                (self.plugin._growth.catalog.thresholds[100],),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        self.sender.image.reset_mock()
        reply=await self.dispatch(f'/解花 {card.id}','bloom-image')
        self.assertIn('解花成功',reply[1])
        self.sender.image.assert_awaited()

    async def test_id_commands_use_owned_ids(self):
        card=next(c for c in self.plugin._cards.cards if c.character_id==1000 and c.rarity=='SSR')
        self.plugin._db.commit_draw('user',[(card.id,card.rarity)],cost=0)
        await self.dispatch('/伙伴 星咲 あかり','id-prep')
        self.sender.image.reset_mock()
        reply=await self.dispatch(f'/卡图 {card.id}','card-image')
        self.assertIn(str(card.id),reply[1])
        self.sender.image.assert_awaited()
        self.sender.image.reset_mock()
        await self.dispatch(f'/好感 星咲 あかり 卡面 {card.id}','affection-card')
        self.sender.image.assert_awaited()
        conn=self.plugin._db._conn
        conn.execute(
            "INSERT OR IGNORE INTO player_cosmetics VALUES"
            "('user','Trophy','10001','test','now')"
        )
        conn.commit()
        equip=await self.dispatch('/装扮 称号 10001','equip-id')
        self.assertIn('10001',equip[1])
        profile=self.plugin._growth.snapshot('user')['player_growth_profile'][0]
        self.assertEqual(profile['title_id'],'10001')

    async def test_catalog_render_failure_falls_back_to_text(self):
        with patch('ongeki_gacha.plugin.render_catalog_index',side_effect=RuntimeError('boom')):
            reply=await self.dispatch('/卡册','catalog-fail')
        self.assertIn('卡册总览',reply[1])
        self.assertTrue(self.sender.text.await_count or self.sender.image.await_count)

    async def test_ceiling_pool_exchange_selects_card(self):
        from ..starter_cards import STARTER_CARD_IDS
        await self.dispatch('/伙伴 星咲 あかり','ceiling-prep')
        entry=next(
            e for e in self.plugin._schedule.entries
            if (e.select_points or 0)>0
            and any(pc.is_select and pc.card_id not in STARTER_CARD_IDS for pc in e.cards.values())
        )
        target=next(pc for pc in entry.cards.values()
                    if pc.is_select and pc.card_id not in STARTER_CARD_IDS)
        card=self.plugin._cards.by_id[target.card_id]
        self.plugin._active_pool_entries=(entry,)
        conn=self.plugin._db._conn
        conn.execute(
            "INSERT INTO pool_select_state"
            "(qq_id,pool_id,select_points,max_select_points,select_claimed,updated_at)"
            " VALUES('user',?,?,?,0,'now')"
            " ON CONFLICT(qq_id,pool_id) DO UPDATE SET select_points=excluded.select_points,"
            " max_select_points=excluded.max_select_points,select_claimed=0",
            (entry.pool_id,entry.select_points,entry.select_points),
        )
        conn.commit()
        original=self.plugin._sync_active_pool
        try:
            self.plugin._sync_active_pool=lambda requested_pool_id='':True
            reply=await self.dispatch(f'/天井池 {entry.pool_id} {card.id}','ceiling-pool')
        finally:
            self.plugin._sync_active_pool=original
        self.assertTrue(reply[0])
        self.assertIn('天井兑换成功',reply[1])
        self.assertIn(str(card.id),reply[1])

    async def test_render_cache_cleanup_only_removes_one_shot_images(self):
        """一次性渲染产物按 TTL 清理；卡池公告图与新鲜产物保留。"""
        root=Path(self.temp.name)
        stale=time.time()-self.plugin.config.ui.render_cache_ttl_seconds-60
        one_shot=(
            'ongeki_draw_1_1.png','ongeki_text_1.png','ongeki_task_1.png',
            'ongeki_reveal_1_1.png','ongeki_item_1.png','ongeki_catalog_1.png',
            'growth_1.png','growth_bloom_1_1.png','growth_1_2.png',
        )
        for name in one_shot:
            path=root/name
            path.write_bytes(b'x')
            os.utime(path,(stale,stale))
        cached=root/'ongeki_pool_notice.png'
        cached.write_bytes(b'x')
        os.utime(cached,(stale,stale))
        fresh=root/'ongeki_draw_1_2.png'
        fresh.write_bytes(b'x')
        self.plugin._cleanup_render_cache()
        remaining={path.name for path in root.glob('*.png')}
        self.assertEqual(remaining,{'ongeki_pool_notice.png','ongeki_draw_1_2.png'})

    async def test_card_index_without_images_is_still_loadable(self):
        """发布包不含卡面：只有索引 JSON 时也应判定为可加载。"""
        root=Path(self.temp.name)/'card_data'
        root.mkdir(parents=True,exist_ok=True)
        info=root/'card_info_merged.json'
        info.write_text(
            '[{"id":100001,"name":"测试卡","rarity":"N","imageFile":"ui_card_100001.png",'
            '"imagePresent":true,"charaId":1000}]',
            encoding='utf-8',
        )
        self.assertTrue(self.plugin._card_data_json_ready(root,info))
        self.assertFalse(self.plugin._card_data_quick_ready(root,info))
        (root/'ui_card_100001.png').write_bytes(b'x')
        self.assertTrue(self.plugin._card_data_quick_ready(root,info))
        self.assertFalse(self.plugin._card_data_json_ready(root,root/'missing.json'))
