import sys, types
decky = types.ModuleType('decky')
decky.logger = type('obj', (object,), {'info': print, 'warning': print, 'error': print, 'exception': print})()
decky.DECKY_PLUGIN_DIR = "."
sys.modules['decky'] = decky

import asyncio
from main import Plugin

async def test():
    p = Plugin()
    mods = p._renodx_mod_list()
    print("Total mods:", len(mods))
    generics = [m for m in mods if m.get("match_type") == "generic_engine"]
    print("Generics:", generics)
    match = p._match_renodx_mod("random_game", mods, "unity")
    print("Match for unity:", match)

asyncio.run(test())
