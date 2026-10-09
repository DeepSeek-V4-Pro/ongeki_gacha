"""1.3.4：谱面身份迁移、终极提交审核和候选边界。"""
import asyncio
from dataclasses import replace
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import AsyncMock

from ..gacha_db import GachaDatabase
from ..task_catalog import CatalogSong, CatalogChart, TaskSelection, _normalize_lxns, pick_random_task


def song():
    return CatalogSong('maimai', '1', 'Test', '', 15, '15', 15, False, False, '', (
        CatalogChart(3, 'standard', 'MASTER 标准', '14+', 14.8),
        CatalogChart(3, 'dx', 'MASTER DX', '14+', 14.9),
        CatalogChart(4, 'dx', 'REMASTER DX', '15', 15),
    ))


class Catalog134Tests(unittest.TestCase):
    def test_standard_and_dx_are_independent_candidates(self):
        s = song()
        selection = pick_random_task([s], 'ultimate', completed_keys={'maimai:1:standard:3','maimai:1:dx:4'})
        self.assertEqual(selection.chart.kind, 'dx')
        self.assertEqual(selection.chart.index, 3)
        self.assertIsNone(pick_random_task([s], 'ultimate', completed_keys={'maimai:1:3','maimai:1:4'}))
        self.assertIsNone(pick_random_task([s], 'ultimate', completed_keys={'maimai:1'}))

    def test_disabled_locked_invalid_and_special_are_excluded(self):
        for s in (replace(song(),disabled=True), replace(song(),locked=True),
                  replace(song(),charts=(CatalogChart(3,'dx','MASTER DX','15',float('inf')),)),
                  replace(song(),charts=(CatalogChart(3,'utage','UTAGE','15',15,True),))):
            self.assertIsNone(pick_random_task([s], 'ultimate'))

    def test_higher_difficulty_stays_in_same_type(self):
        s = song()
        self.assertNotIn('或以上',TaskSelection(s,s.charts[0]).requirement)
        self.assertIn('或以上',TaskSelection(s,s.charts[1]).requirement)
        self.assertNotIn('或以上',TaskSelection(s,s.charts[1]).exact_requirement)

    def test_lxns_types_zero_index_and_no_constant(self):
        raw = [{'id':1,'title':'Test','difficulties':{
            'standard':[{'difficulty':3,'level':'14+','level_value':14.8}],
            'dx':[{'difficulty':3,'level':'14+','level_value':14.9},
                  {'difficulty':0,'level':'1','level_value':1},
                  {'difficulty':4,'level':'15','level_value':0}]}}]
        charts = _normalize_lxns('maimai',raw,'')[0].charts
        self.assertEqual([c.index for c in charts],[3,3,0])
        self.assertEqual([c.label for c in charts][:2],['MASTER 标准','MASTER DX'])
        self.assertEqual(_normalize_lxns('maimai',[{'id':2,'title':'Test','difficulties':{
            'dx':[{'difficulty':3,'level':'15','level_value':float('nan')}]
        }}],''),[])


class Database134Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = GachaDatabase(Path(self.temp.name)/'test.db')
        self.db.open()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def create(self, variant='standard', kind='ultimate', user='u'):
        return self.db.create_task(user, task_kind=kind,game='maimai',song_id='1',song_title='Test',
                                   difficulty_index=3, difficulty_label='MASTER DX' if variant=='dx' else 'MASTER',
                                   chart_type=variant)

    def test_both_types_award_once_even_after_restart_and_cleanup(self):
        for variant in ('standard','dx'):
            receipt = self.create(variant)
            self.assertTrue(receipt.success,receipt.error)
            self.assertTrue(self.db.submit_task(receipt.task_id,'u',ultimate=True).success)
            self.assertTrue(self.db.complete_ultimate(receipt.task_id,'admin',reward=30000).success)
            self.assertFalse(self.db.complete_ultimate(receipt.task_id,'admin',reward=30000).success)
            self.db.close(); self.db.open()
        self.assertEqual(self.db.get_player('u').points,60000)
        self.db.cleanup_task_history('2099-01-01',retention_days=0)
        self.db.close(); self.db.open()
        self.assertEqual(self.db.get_ultimate_completed_keys('u'),{'maimai:1:standard:3','maimai:1:dx:3'})
        self.assertFalse(self.create('standard').success)
        self.assertFalse(self.create('dx').success)

    def test_submit_type_owner_state_and_reject_resubmit(self):
        task = self.create()
        self.assertFalse(self.db.submit_task(task.task_id,'other',ultimate=True).success)
        self.assertFalse(self.db.submit_task(task.task_id,'u',ultimate=False).success)
        self.assertFalse(self.db.complete_ultimate(task.task_id,'admin',reward=1).success)
        self.assertTrue(self.db.submit_task(task.task_id,'u',ultimate=True).success)
        self.assertFalse(self.db.submit_task(task.task_id,'u',ultimate=True).success)
        self.assertFalse(self.db.approve_task(task.task_id,'admin',grade='SSS+',reward=1).success)
        self.assertTrue(self.db.reject_task(task.task_id,'admin').success)
        self.assertFalse(self.create('dx').success)
        self.db.cleanup_task_history('2099-01-01',retention_days=0)
        self.assertIsNotNone(self.db.get_task(task.task_id))
        self.assertTrue(self.db.submit_task(task.task_id,'u',ultimate=True).success)
        self.assertTrue(self.db.complete_ultimate(task.task_id,'admin',reward=30000,note='checked').success)
        self.assertEqual(self.db.get_task(task.task_id).note,'checked')

    def test_migration_recovers_type_but_unknown_records_keep_lock(self):
        task = self.create('dx')
        self.db.submit_task(task.task_id,'u')
        self.db.complete_ultimate(task.task_id,'admin',reward=30000)
        conn = self.db._conn
        # Rebuild the exact 1.3.3 completion schema, including a record whose task was cleaned.
        conn.executescript("""ALTER TABLE ultimate_completed_charts RENAME TO scratch;
            CREATE TABLE ultimate_completed_charts (
                qq_id TEXT NOT NULL,game TEXT NOT NULL,song_id TEXT NOT NULL,
                difficulty_index INTEGER NOT NULL,completed_at TEXT NOT NULL,
                PRIMARY KEY(qq_id,game,song_id,difficulty_index));
            INSERT INTO ultimate_completed_charts SELECT qq_id,game,song_id,difficulty_index,completed_at FROM scratch;
            DROP TABLE scratch;
            ALTER TABLE tasks DROP COLUMN chart_type;
            INSERT INTO ultimate_completed_charts VALUES('u','maimai','unknown',3,'old');""")
        self.db.close(); self.db.open(); self.db.close(); self.db.open()
        self.assertEqual(self.db.get_ultimate_completed_keys('u'),{'maimai:1:dx:3','maimai:unknown:3'})
        self.assertTrue(self.create('standard').success)
        self.assertEqual(self.db.get_player('u').points,30000)
        self.assertEqual(self.db._conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_progress_recovers_missing_or_stale_pointer(self):
        task = self.create()
        self.db._conn.execute("UPDATE ultimate_progress SET active_task_id=NULL WHERE qq_id='u'")
        self.assertEqual(self.db.get_ultimate_progress('u').active_task_id,task.task_id)
        self.assertFalse(self.create('dx').success)
        self.db.reset_task(task.task_id,'admin')
        self.assertIsNone(self.db.get_ultimate_progress('u').active_task_id)
        self.assertTrue(self.create('dx').success)

    def test_unknown_difficulty_locks_whole_song(self):
        self.db.get_player('u')
        self.db._conn.execute("INSERT INTO ultimate_completed_charts VALUES('u','maimai','1',-1,'old','dx')")
        self.assertFalse(self.create('standard').success)
        self.assertFalse(self.create('dx').success)
        self.assertEqual(self.db.get_ultimate_completed_keys('u'),{'maimai:1'})


try:
    from ..plugin import OngekiGachaPlugin
except ModuleNotFoundError as exc:
    if exc.name != 'maibot_sdk':
        raise
    OngekiGachaPlugin = None


@unittest.skipIf(OngekiGachaPlugin is None, '需要 maibot_sdk')
class Commands134Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.plugin=OngekiGachaPlugin()
        self.plugin.set_plugin_config(self.plugin.build_default_config())
        self.plugin._db=GachaDatabase(Path(self.temp.name)/'db')
        self.plugin._db.open()
        self.plugin._send_text=AsyncMock()
        self.plugin._send_item_gain_card=AsyncMock()
        self.plugin._user_id=lambda kwargs:kwargs.get('user_id','u')
        self.plugin._is_admin=lambda uid:uid=='admin'
        self.plugin._has_photo=lambda kwargs:kwargs.get('photo',False)
        self.plugin._get_task_catalog=AsyncMock(return_value=[song()])
        self.plugin._lock=asyncio.Lock()
        # AstrBot identity binding is irrelevant for these isolated command tests.
        self.plugin._binding_hint=lambda uid:''
        self.plugin._display_user_id=lambda uid:uid

    async def asyncTearDown(self):
        self.plugin._db.close();self.temp.cleanup()

    async def dispatch(self,text,**kwargs):
        for component in self.plugin.get_components():
            if component['type']!='COMMAND':continue
            metadata=component['metadata'];match=re.fullmatch(metadata['command_pattern'],text)
            if match:
                return (await getattr(self.plugin,metadata['handler_name'])(
                    stream_id='mock',matched_groups=match.groupdict(),**kwargs))[1]
        self.fail(f'未注册命令 {text}')

    def task(self,kind='ultimate'):
        return self.plugin._db.create_task('u',task_kind=kind,game='maimai',song_id='1',song_title='Test',
            difficulty_index=3,difficulty_label='MASTER DX',chart_type='dx').task_id

    async def test_ultimate_submission_review_rejection_and_repeat_reward(self):
        tid=self.task()
        self.assertIn('/终极提交',await self.dispatch(f'/任务完成 {tid}'))
        self.assertIn('同一条消息',await self.dispatch(f'/终极提交 {tid}'))
        self.assertIn('/终极审核',await self.dispatch(f'/终极提交 {tid}',photo=True))
        self.assertIn('仅管理员',await self.dispatch(f'/终极审核 {tid} 通过'))
        self.assertIn('/终极审核',await self.dispatch(f'/任务审核 {tid} SSS+',user_id='admin'))
        self.assertIn('已驳回',await self.dispatch(f'/终极审核 {tid} 拒绝 图不清楚',user_id='admin'))
        await self.dispatch(f'/终极提交 {tid}',photo=True)
        self.assertIn('已发放',await self.dispatch(f'/终极审核 {tid} 通过',user_id='admin'))
        await self.dispatch(f'/终极审核 {tid} 通过',user_id='admin')
        self.assertEqual(self.plugin._db.get_player('u').points,self.plugin.config.task.ultimate_reward)

    async def test_normal_submission_does_not_suggest_ss_grade(self):
        tid=self.task('normal')
        self.assertIn('/任务完成',await self.dispatch(f'/终极提交 {tid}',photo=True))
        text=await self.dispatch(f'/任务完成 {tid}',photo=True)
        self.assertIn(f'/任务审核 {tid} 普通|拒绝',text)
        self.assertNotIn('SSS',text)

    async def test_unavailable_candidates_do_not_mark_finished(self):
        self.task('normal')
        text=await self.dispatch('/接任务 终极 舞萌')
        self.assertNotIn('全部完成',text)
        self.assertFalse(self.plugin._db.get_ultimate_progress('u').finished)
        self.assertIn('游戏可选',await self.dispatch('/接任务 普通 typo'))

    async def test_old_active_ultimate_stays_visible(self):
        tid=self.task()
        for i in range(110):
            t=self.plugin._db.create_task('u',task_kind='normal',game='maimai',song_id=str(i+2),song_title='History',normal_limit=200)
            self.plugin._db.reset_task(t.task_id,'admin')
        text=await self.dispatch('/任务列表')
        self.assertIn(f'#{tid} 终极任务',text)

    async def test_requirements_and_reward_text(self):
        s=song();sel=TaskSelection(s,s.charts[1])
        text=self.plugin._task_card_text(1,sel,'ultimate')
        self.assertIn('仅 MASTER DX',text)
        self.assertIn('/终极提交 1',text)
        self.assertNotIn('或以上',text)
        self.assertNotIn('解花券',self.plugin._task_requirement(sel,'advanced'))
        self.assertIn('起（按评级）',self.plugin._task_card_text(2,sel,'challenge'))

    async def test_daily_expiry_respects_auto_reset_and_preserves_submitted(self):
        tid=self.task('normal')
        self.plugin._db._conn.execute("UPDATE tasks SET task_date='2000-01-01' WHERE id=?",(tid,))
        self.assertIn('不可提交',await self.dispatch(f'/任务完成 {tid}',photo=True))
        self.plugin.config.task.auto_reset=False
        tid=self.task('normal')
        self.plugin._db._conn.execute("UPDATE tasks SET task_date='2000-01-01' WHERE id=?",(tid,))
        await self.dispatch(f'/任务完成 {tid}',photo=True)
        self.assertEqual(self.plugin._db.get_task(tid).status,'submitted')
        self.plugin.config.task.auto_reset=True
        text=await self.dispatch('/任务列表')
        self.assertIn(f'#{tid}',text)
        self.assertEqual(self.plugin._db.get_task(tid).status,'submitted')


if __name__=='__main__':
    unittest.main()
