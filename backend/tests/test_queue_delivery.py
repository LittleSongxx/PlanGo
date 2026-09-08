"""Real disposable Redis: failed dead-letter publication must retain the PEL entry."""
import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from plango_harness.queue import RunQueue
from redis.exceptions import ResponseError


@unittest.skipUnless(shutil.which('redis-server'), 'local Redis executable is not installed')
class QueueDeliveryCheck(unittest.IsolatedAsyncioTestCase):
    async def test_dead_letter_publish_ack_and_retry_are_atomic(self):
        with tempfile.TemporaryDirectory(prefix='plango-queue-') as work:
            socket = Path(work) / 'redis.sock'
            process = subprocess.Popen(['redis-server', '--port', '0', '--unixsocket', str(socket), '--save', '', '--appendonly', 'no'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            queue = RunQueue('unix://' + str(socket), 'plango:test-runs', 'test-workers', allow_fallback=False)
            try:
                for _ in range(100):
                    if socket.exists():
                        break
                    await asyncio.sleep(.02)
                await queue.connect()
                client = queue.client
                item = await queue.enqueue('isolated-run', local=False)
                await client.xreadgroup(queue.group, queue.consumer, {queue.stream: '>'})
                await client.hset(queue._attempt_key, item.stream_id, 3)
                dead = queue.stream + ':dead-letter'
                await client.set(dead, 'intentional-wrong-type')
                with self.assertRaises(ResponseError):
                    await queue.dead_letter(item, 3, RuntimeError('private-detail'))
                self.assertEqual((await client.xpending(queue.stream, queue.group))['pending'], 1)
                self.assertEqual(await client.hget(queue._attempt_key, item.stream_id), '3')
                await client.delete(dead)
                await queue.dead_letter(item, 4, RuntimeError('private-detail'))
                self.assertEqual((await client.xpending(queue.stream, queue.group))['pending'], 0)
                self.assertIsNone(await client.hget(queue._attempt_key, item.stream_id))
                entries = await client.xrange(dead)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0][1]['stream_id'], item.stream_id)
                self.assertEqual(entries[0][1]['error'], 'RuntimeError')
                self.assertNotIn('private-detail', str(entries))
                await queue.dead_letter(item, 4, RuntimeError('retry after lost response'))
                self.assertEqual(await client.xlen(dead), 1)
            finally:
                await queue.close()
                process.terminate()
                process.wait(timeout=5)
