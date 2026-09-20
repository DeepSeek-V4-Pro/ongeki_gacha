import asyncio
import copy
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ..gacha_core import load_cards
from ..gacha_db import GachaDatabase
from ..growth_catalog import GrowthCatalog
from ..voice_service import VoiceCatalog, VoiceService


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        root=Path(__file__).parents[1]
        self.catalog=VoiceCatalog(root/'assets/growth')
        self.growth_catalog=GrowthCatalog(root/'assets/growth',load_cards(root/'assets/card_data/card_info_merged.json'))
        self.snapshot={'affection_reward_claims':[], 'player_characters':[]}
        self.growth=SimpleNamespace(catalog=self.growth_catalog,snapshot=lambda qq:copy.deepcopy(self.snapshot))
        self.sender=SimpleNamespace(custom=AsyncMock(return_value={'success':True}))
        self.now=100.0
        self.service=VoiceService(self.growth,self.catalog,self.sender,enabled=True,clock=lambda:self.now)
        self.temp=tempfile.TemporaryDirectory()
        self.catalog.root=Path(self.temp.name)
        self.row=self.catalog.voices[(1000,1)]
        path=Path(self.temp.name)/'sample.wav';path.write_bytes(b'test-opaque-audio')
        self.row.update(path='sample.wav',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),listening_review='verified')

    def tearDown(self):self.temp.cleanup()

    def unlock(self):
        self.snapshot['affection_reward_claims'].append({'character_id':1000,'reward_key':self.row['reward_key']})

    async def test_automatic_uses_event_not_profile_claim(self):
        self.catalog.events[(1000,'level_up')]=dict(self.row,voice_id=124)
        reserved=[]
        def event(user,request,claim=False):
            if reserved:return None
            if claim:reserved.append(request)
            return {'character_id':1000,'event':'level_up'}
        self.growth.automatic_voice_event=event
        self.assertTrue((await self.service.automatic('u','event','g'))['success'])
        self.assertFalse((await self.service.automatic('u','event','g'))['success'])
        self.sender.custom.assert_awaited_once()
        self.assertEqual(reserved,['event'])
        self.assertEqual(self.snapshot['affection_reward_claims'],[])

    async def test_automatic_pending_disabled_and_failure(self):
        self.catalog.events[(1000,'level_up')]=dict(self.row,voice_id=124,listening_review='pending')
        calls=[]
        def event(user,request,claim=False):
            if claim:calls.append(request)
            return {'character_id':1000,'event':'level_up'}
        self.growth.automatic_voice_event=event
        self.assertEqual((await self.service.automatic('u','pending','g'))['state'],'unavailable')
        self.assertEqual(calls,[])
        self.catalog.events[(1000,'level_up')]['listening_review']='verified'
        self.service.automatic_enabled=False
        self.assertEqual((await self.service.automatic('u','off','g'))['state'],'disabled')
        self.service.automatic_enabled=True;self.sender.custom.return_value={'success':False}
        self.assertEqual((await self.service.automatic('u','fail','g'))['state'],'failed')
        self.assertEqual(calls,['fail'])

    async def test_lock_then_send_replay_and_no_mutation(self):
        result=await self.service.play('u',1000,1,'req','group')
        self.assertFalse(result['success']);self.sender.custom.assert_not_called()
        self.unlock();before=copy.deepcopy(self.snapshot)
        first=await self.service.play('u',1000,1,'req','group')
        self.assertTrue(first['success'])
        self.assertEqual(first,await self.service.play('u',1000,1,'req','group'))
        self.sender.custom.assert_awaited_once()
        self.assertEqual(self.snapshot,before)

    async def test_shared_cooldown_and_minute_limit(self):
        self.unlock()
        for i in range(5):
            self.now=100+i*10
            self.assertTrue((await self.service.play('u',1000,1,str(i),f'group{i}'))['success'])
        self.now=150
        self.assertFalse((await self.service.play('u',1000,1,'sixth','private'))['success'])
        self.now=161
        self.assertTrue((await self.service.play('u',1000,1,'next','private'))['success'])

    async def test_failed_sdk_dict_is_not_success(self):
        self.unlock();self.sender.custom.return_value={'success':False}
        result=await self.service.play('u',1000,1,'req','group')
        self.assertFalse(result['success']);self.assertEqual(result['state'],'failed')

    async def test_sdk_bool_success_and_timeout_unknown(self):
        self.unlock();self.sender.custom.return_value=True
        self.assertTrue((await self.service.play('u',1000,1,'bool','g'))['success'])
        self.now=200
        def consume_and_timeout(awaitable,timeout):
            awaitable.close();raise TimeoutError
        with patch('ongeki_gacha.voice_service.asyncio.wait_for',side_effect=consume_and_timeout):
            result=await self.service.play('u',1000,1,'timeout','g')
        self.assertFalse(result['success']);self.assertEqual(result['state'],'unknown')
        self.assertEqual((await self.service.play('u',1000,1,'timeout','g'))['state'],'unknown')
        self.assertEqual(self.sender.custom.call_count,2)
        self.assertEqual(self.sender.custom.await_count,1)

    async def test_missing_tampered_and_unreviewed(self):
        self.unlock();self.row['listening_review']='pending'
        self.assertFalse((await self.service.play('u',1000,1,'a','g'))['success'])
        self.row['listening_review']='verified';self.row['sha256']='wrong'
        self.assertFalse((await self.service.play('u',1000,1,'b','g'))['success'])
        self.row['path']='missing.wav'
        self.assertFalse((await self.service.play('u',1000,1,'c','g'))['success'])
        self.sender.custom.assert_not_called()

    async def test_concurrent_same_message_only_sends_once(self):
        self.unlock();entered=asyncio.Event();release=asyncio.Event()
        async def hold(*args):entered.set();await release.wait();return True
        self.sender.custom.side_effect=hold
        first=asyncio.create_task(self.service.play('u',1000,1,'req','g'))
        await entered.wait()
        second=await self.service.play('u',1000,1,'req','g')
        self.assertEqual(second['state'],'sending')
        release.set();self.assertTrue((await first)['success'])
        self.sender.custom.assert_awaited_once()

    async def test_limits_and_requests_survive_restart(self):
        self.unlock()
        db=GachaDatabase(Path(self.temp.name)/'voice-state.db')
        db.open()
        try:
            self.growth.db=db
            service=VoiceService(self.growth,self.catalog,self.sender,enabled=True,clock=lambda:self.now)
            for i in range(5):
                self.now=100+i*10
                self.assertTrue((await service.play('u',1000,1,str(i),f'g{i}'))['success'])
            restarted=VoiceService(self.growth,self.catalog,self.sender,enabled=True,clock=lambda:self.now)
            self.now=150
            self.assertFalse((await restarted.play('u',1000,1,'sixth','private'))['success'])
            replayed=await restarted.play('u',1000,1,'0','g0')
            self.assertTrue(replayed['success'])
            self.assertEqual(replayed['state'],'sent')
            self.assertEqual(self.sender.custom.await_count,5)
        finally:
            db.close()


if __name__=='__main__':unittest.main()


class VoicePersistenceRegressionTests(unittest.IsolatedAsyncioTestCase):
    setUp = VoiceTests.setUp
    unlock = VoiceTests.unlock

    def tearDown(self):
        if getattr(self.growth, 'db', None) is not None:
            self.growth.db.close()
        self.temp.cleanup()

    def persistent_service(self, **limits):
        db=GachaDatabase(Path(self.temp.name)/'voice-regression.db')
        db.open()
        self.addCleanup(db.close)
        self.growth.db=db
        self.service=VoiceService(self.growth,self.catalog,self.sender,enabled=True,
                                  clock=lambda:self.now,**limits)
        self.unlock()
        return db

    async def test_short_request_ttl_does_not_clear_minute_limit(self):
        self.persistent_service(cooldown_seconds=0, minute_limit=2, request_ttl_seconds=1)
        for i in range(2):
            self.now=100+i*2
            self.assertTrue((await self.service.play('u',1000,1,str(i),'g'))['success'])
        self.now=104
        self.assertFalse((await self.service.play('u',1000,1,'third','g'))['success'])

    async def test_cooldown_longer_than_minute(self):
        self.persistent_service(cooldown_seconds=120)
        self.assertTrue((await self.service.play('u',1000,1,'first','g'))['success'])
        self.now=161
        self.assertFalse((await self.service.play('u',1000,1,'second','g'))['success'])

    async def test_default_clock_persists_epoch_timestamp(self):
        import time
        db=self.persistent_service()
        service=VoiceService(self.growth,self.catalog,self.sender,enabled=True)
        before=time.time()
        self.assertTrue((await service.play('u',1000,1,'epoch','g'))['success'])
        recorded=db.get_timed_event('voice_request','u:epoch')[0]
        self.assertGreaterEqual(recorded,before)
        self.assertLessEqual(recorded,time.time())
