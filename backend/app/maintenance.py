import asyncio
import sys

from .db import connect, disconnect
from .worker import cleanup_expired, reconcile_stuck


async def main(command:str)->None:
    await connect()
    try:
        if command=="cleanup":await cleanup_expired({})
        elif command=="reconcile":await reconcile_stuck({})
        else:raise SystemExit("Usage: python -m app.maintenance [cleanup|reconcile]")
    finally:await disconnect()
if __name__=="__main__":asyncio.run(main(sys.argv[1] if len(sys.argv)>1 else ""))
