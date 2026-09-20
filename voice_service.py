"""角色语音索引与SDK发送适配；不持有数据库锁发送，不自动重试未知结果。"""
from __future__ import annotations

import asyncio
import base64
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import time

from .growth_core import MAIN_CHARACTER_IDS


class VoiceCatalog:
    def __init__(self, root: Path):
        self.root=root.resolve()
        data=json.loads((root/'voice_catalog.json').read_text(encoding='utf8'))
        self.voices={(int(v['character_id']),int(v['sequence'])):v for v in data['voices']}
        expected={(cid,number) for cid in MAIN_CHARACTER_IDS for number in range(1,11)}
        if set(self.voices)!=expected or len(data['voices'])!=170:
            raise ValueError('语音索引必须恰好覆盖17名角色各10条')
        for row in self.voices.values():
            path=(self.root/row['path']).resolve()
            if not path.is_relative_to(self.root):raise ValueError('语音资源路径越界')
        event_path=root/'event_voice_catalog.json'
        self.events={}
        if event_path.is_file():
            rows=json.loads(event_path.read_text(encoding='utf8'))['voices']
            self.events={(int(r['character_id']),r['event']):r for r in rows}
            if len(rows)!=51 or set(self.events)!={(cid,event) for cid in MAIN_CHARACTER_IDS for event in ('gift_small','gift_large','level_up')}:
                raise ValueError('养成事件语音索引必须覆盖17名角色各3条')
            for row in rows:
                if row['cue_id']!={'gift_small':122,'gift_large':123,'level_up':124}[row['event']]:
                    raise ValueError('养成事件语音cue错误')

    def validate_rewards(self, growth_catalog):
        for (cid,_),row in self.voices.items():
            rewards=[r for r in growth_catalog.characters[cid]['rewards'] if r['kind']=='ProfileVoice'
                     and int(r['id'])==row['voice_id'] and r['reward_key']==row['reward_key']
                     and int(r['level'])==row['unlock_level']]
            if len(rewards)!=1:raise ValueError('语音与好感奖励索引不一致')

    def audio(self, row: dict) -> bytes:
        if row.get('listening_review')!='verified':
            raise ValueError('已解锁，语音待试听验收，暂不可用')
        path=(self.root/row['path']).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError('已解锁，语音资源暂不可用')
        if path.stat().st_size>16*1024*1024:
            raise ValueError('语音超过本插件单次发送预算')
        audio=path.read_bytes()
        if not audio or hashlib.sha256(audio).hexdigest()!=row['sha256']:
            raise ValueError('已解锁，语音资源校验失败')
        return audio


class VoiceService:
    def __init__(
        self,
        growth,
        catalog: VoiceCatalog,
        sender,
        *,
        enabled=False,
        automatic_enabled=True,
        clock=time.time,
        cooldown_seconds=10,
        minute_limit=5,
        request_ttl_seconds=600,
        inflight_limit=10,
        send_timeout_seconds=30,
    ):
        self.growth=growth;self.catalog=catalog;self.sender=sender;self.enabled=enabled
        catalog.validate_rewards(growth.catalog)
        self.clock=clock
        self.automatic_enabled=automatic_enabled
        self.apply_limits(
            cooldown_seconds=cooldown_seconds,
            minute_limit=minute_limit,
            request_ttl_seconds=request_ttl_seconds,
            inflight_limit=inflight_limit,
            send_timeout_seconds=send_timeout_seconds,
        )
        self._attempts=defaultdict(deque)
        self._inflight=defaultdict(int)
        self._requests={}
        self._lock=asyncio.Lock()

    def apply_limits(
        self,
        *,
        cooldown_seconds: int,
        minute_limit: int,
        request_ttl_seconds: int,
        inflight_limit: int,
        send_timeout_seconds: int,
    ) -> None:
        """热更新语音限流参数；已持久化的历史次数不受影响。"""
        self.cooldown_seconds=max(int(cooldown_seconds),0)
        self.minute_limit=max(int(minute_limit),1)
        self.request_ttl_seconds=max(int(request_ttl_seconds),1)
        self.inflight_limit=max(int(inflight_limit),1)
        self.send_timeout_seconds=max(int(send_timeout_seconds),1)

    def _timed_db(self):
        return getattr(self.growth,'db',None)

    async def play(self, qq_id: str, cid: int, number: int, request_id: str, stream_id: str):
        if cid not in MAIN_CHARACTER_IDS:return {'success':False,'error':'该角色不开放好感语音'}
        row=self.catalog.voices.get((cid,number))
        if row is None:return {'success':False,'error':'语音序号 1～10（/角色语音 分类）'}
        snapshot=await asyncio.to_thread(self.growth.snapshot,qq_id)
        claimed={r['reward_key'] for r in snapshot['affection_reward_claims'] if r['character_id']==cid}
        if row['reward_key'] not in claimed:
            from .growth_core import affection_level
            points=next((r['affection_points'] for r in snapshot['player_characters'] if r['character_id']==cid),0)
            level=affection_level(points,self.growth.catalog.thresholds)
            return {'success':False,'error':(
                f"语音未解锁 Lv{level}/{row['unlock_level']}（/送礼 提升好感）"
            )}
        if not self.enabled:return {'success':False,'error':'语音发送已在配置中关闭'}
        if not request_id:return {'success':False,'error':'缺少消息ID，未发送语音'}
        try:
            payload=await asyncio.to_thread(self.catalog.audio,row)
        except (ValueError,OSError) as exc:
            return {'success':False,'error':str(exc)}
        return await self._send(qq_id,row,payload,request_id,stream_id)

    async def automatic(self, qq_id, request_id, stream_id):
        if not self.enabled or not self.automatic_enabled:return {'success':False,'state':'disabled'}
        event=await asyncio.to_thread(self.growth.automatic_voice_event,qq_id,request_id)
        if not event:return {'success':False,'state':'skipped'}
        row=self.catalog.events.get((event['character_id'],event['event']))
        if row is None:return {'success':False,'state':'unavailable'}
        try:payload=await asyncio.to_thread(self.catalog.audio,row)
        except (ValueError,OSError):return {'success':False,'state':'unavailable'}
        async def reserve():
            return await asyncio.to_thread(self.growth.automatic_voice_event,qq_id,request_id,claim=True)
        return await self._send(qq_id,row,payload,'automatic:'+request_id,stream_id,reserve=reserve)

    async def _load_request(self, qq_id, request_id):
        """读取请求去重状态；有数据库时跨重启保留。"""
        key=f"{qq_id}:{request_id}"
        db=self._timed_db()
        if db is not None:
            row=await asyncio.to_thread(db.get_timed_event,'voice_request',key)
            if row is None:return None
            _,payload=row
            try:return json.loads(payload)
            except (TypeError,ValueError):return None
        entry=self._requests.get((qq_id,request_id))
        if entry is None or self.clock()-entry[0]>=self.request_ttl_seconds:
            return None
        return entry[1]

    async def _store_request(self, qq_id, request_id, occurred_at, response):
        key=f"{qq_id}:{request_id}"
        db=self._timed_db()
        data=json.dumps(response,ensure_ascii=False,separators=(',',':'))
        if db is not None:
            await asyncio.to_thread(
                db.add_timed_event,qq_id,'voice_request',occurred_at,
                payload=data,event_key=key,
            )
            return
        self._requests[(qq_id,request_id)]=(occurred_at,response)

    async def _load_attempts(self, qq_id, since):
        db=self._timed_db()
        if db is not None:
            rows=await asyncio.to_thread(
                db.list_timed_events,'voice_attempt',qq_id=qq_id,since=since,
            )
            return [occurred_at for occurred_at,_ in rows]
        attempts=self._attempts[qq_id]
        while attempts and attempts[0]<since:attempts.popleft()
        return list(attempts)

    async def _record_attempt(self, qq_id, occurred_at):
        db=self._timed_db()
        if db is not None:
            await asyncio.to_thread(
                db.add_timed_event,qq_id,'voice_attempt',occurred_at,
            )
            return
        self._attempts[qq_id].append(occurred_at)

    async def _send(self, qq_id, row, payload, request_id, stream_id, *, reserve=None):
        now=self.clock()
        async with self._lock:
            db=self._timed_db()
            if db is not None:
                # 请求去重与限流各有保留窗口，短 TTL 不能删除一分钟次数。
                await asyncio.to_thread(db.prune_timed_events,
                    now-self.request_ttl_seconds,kind='voice_request')
                await asyncio.to_thread(db.prune_timed_events,
                    now-max(60,self.cooldown_seconds),kind='voice_attempt')
            # 请求状态跨重启保留；同一请求在发送中也不能并发再发。
            previous=await self._load_request(qq_id,request_id)
            if previous is not None:
                if previous.get('voice_id')!=row['voice_id']:
                    return {'success':False,'error':'此消息ID已用于另一条语音'}
                return previous
            attempts=await self._load_attempts(qq_id,now-max(60,self.cooldown_seconds))
            if attempts and now-attempts[-1]<self.cooldown_seconds:
                return {'success':False,'error':f'语音冷却{self.cooldown_seconds}秒，请稍后再试'}
            if sum(at >= now-60 for at in attempts)>=self.minute_limit:
                return {'success':False,'error':f'每分钟最多点播{self.minute_limit}次'}
            if self._inflight[stream_id]>=self.inflight_limit:
                return {'success':False,'error':'当前会话语音队列已满'}
            if reserve is not None and not await reserve():return {'success':False,'state':'skipped'}
            await self._record_attempt(qq_id,now)
            self._inflight[stream_id]+=1
            await self._store_request(qq_id,request_id,now,{
                'success':False,'state':'sending','voice_id':row['voice_id'],
                'error':'该请求正在发送，请勿重复点播',
            })
        cancelled=False
        try:
            result=await asyncio.wait_for(
                self.sender.custom('voice',base64.b64encode(payload).decode('ascii'),stream_id),
                timeout=self.send_timeout_seconds,
            )
            success=result is True or isinstance(result,dict) and (result.get('sent') is True or result.get('success') is True)
            response={'success':bool(success),'state':'sent' if success else 'failed'}
            if not success:response['error']='语音发送失败，未扣任何养成资源'
        except (TimeoutError,asyncio.CancelledError) as exc:
            cancelled=isinstance(exc,asyncio.CancelledError)
            response={'success':False,'state':'unknown','error':'发送结果尚不确定，未自动重试；稍后可主动重新点播'}
        except Exception:
            response={'success':False,'state':'failed','error':'语音发送失败，未扣任何养成资源'}
        finally:
            async with self._lock:
                self._inflight[stream_id]-=1
                if self._inflight[stream_id]==0:del self._inflight[stream_id]
        response['voice_id']=row['voice_id']
        async with self._lock:
            await self._store_request(qq_id,request_id,self.clock(),response)
        if cancelled:raise asyncio.CancelledError
        return response
