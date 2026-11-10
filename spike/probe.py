import asyncio
import base64
import json
import math
import time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent


async def main():
    async with websockets.connect('ws://127.0.0.1:8765', max_size=16 * 1024 * 1024) as ws:
        counter = 0

        async def call(command, **args):
            nonlocal counter
            counter += 1
            await ws.send(json.dumps(dict(command=command, id=counter, **args)))
            response = json.loads(await asyncio.wait_for(ws.recv(), 30))
            assert response['id'] == counter
            return response

        async def ok(command, **args):
            response = await call(command, **args)
            assert response['ok'], response
            return response['result']

        async def screenshot(name):
            image = await ok('screenshot')
            (ROOT / name).write_bytes(base64.b64decode(image.pop('png_base64')))
            assert (image['width'], image['height']) == (640, 480), image
            return image

        before = await ok('state')
        print('BEFORE', json.dumps(before), flush=True)
        assert before['in_world'] and not before['creative']
        assert before['render_distance'] == 4
        capture = await screenshot('before.png')
        await ok('key', code=87, duration_ms=1000)
        await asyncio.sleep(1.5)
        after = await ok('state')
        await screenshot('after.png')
        distance = math.dist([before[k] for k in ('x', 'y', 'z')], [after[k] for k in ('x', 'y', 'z')])
        assert distance > 0.1, (before, after)
        await asyncio.sleep(0.5)
        stopped = await ok('state')
        assert math.dist([after[k] for k in ('x', 'z')], [stopped[k] for k in ('x', 'z')]) < 0.1
        await ok('mouse', dx=100, dy=20)
        look = await ok('state')
        assert abs(look['yaw'] - stopped['yaw']) > 1
        for button in (0, 1):
            await ok('mouse', button=button, pressed=True)
            await asyncio.sleep(0.2)
            await ok('mouse', button=button, pressed=False)
        await ok('key', code=87, pressed=True)
        await asyncio.sleep(0.1)
        await ok('key', code=87, pressed=False)
        invalid = await call('nonexistent')
        assert not invalid['ok']
        await asyncio.sleep(10)
        await ok('fps_reset')
        start = time.monotonic()
        fps = []
        for _ in range(30):
            await asyncio.sleep(1)
            fps.append((await ok('state'))['fps'])
        measured = await ok('state')
        await screenshot('final.png')
        report = dict(before=before, after=after, stopped=stopped, look=look,
                      distance=distance, capture=capture, invalid=invalid,
                      fps_samples=fps, fps_average=sum(fps) / len(fps),
                      rendered_frames=measured['frames'], seconds=measured['elapsed_seconds'],
                      effective_fps=measured['frames'] / measured['elapsed_seconds'],
                      wall_seconds=time.monotonic() - start)
        (ROOT / 'results.json').write_text(json.dumps(report, indent=2))
        print('AFTER', json.dumps(after), flush=True)
        print('PASS movement_m=%.3f fps_mean=%.2f effective_fps=%.2f range=%d..%d' %
              (distance, report['fps_average'], report['effective_fps'], min(fps), max(fps)), flush=True)


asyncio.run(main())
