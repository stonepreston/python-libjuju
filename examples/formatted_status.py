"""
This example demonstrates how to obtain a formatted full status
description. For a similar solution using the FullStatus object
check examples/fullstatus.py
"""
import asyncio
from juju import loop
import logging
import sys
from logging import getLogger
from juju.model import Model
import tempfile

LOG = getLogger(__name__)
logging.basicConfig(stream=sys.stdout, level=logging.INFO)


async def main():
    model = Model()
    await model.connect_current()

    application = await model.deploy(
        'cs:ubuntu-10',
        application_name='ubuntu',
        series='trusty',
        channel='stable',
    )

    await asyncio.sleep(10)
    tmp = tempfile.NamedTemporaryFile(delete=False)
    LOG.info('status dumped to %s', tmp.name)
    with open(tmp.name, 'w') as f:
        for i in range(10):
            # Uncomment this line to get the full status
            # using the standard output.
            # await model.formatted_status(target=sys.stdout)
            await model.formatted_status(target=f)
            f.write('-----------\n')
    await application.remove()
    await model.disconnect()

if __name__ == '__main__':
    loop.run(main())
