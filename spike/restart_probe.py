import asyncio
import json
from pathlib import Path

import websockets


async def main():
    async with websockets.connect('ws://127.0.0.1:8765') as ws:
        await ws.send(json.dumps({'command': 'state'}))
        response = json.loads(await asyncio.wait_for(ws.recv(), 30))
        state = response['result']
        assert response['ok'] and state['in_world'] and not state['creative']
        assert state['render_distance'] == 4
        assert abs(state['y'] - 135) < 0.1
        assert 'llvmpipe' in state['renderer']
        Path(__file__).with_name('restart-state.json').write_text(json.dumps(state, indent=2))
        print('PASS automatic_saved_world_reload ' + json.dumps(state))


asyncio.run(main())
