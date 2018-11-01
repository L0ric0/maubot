# maubot - A plugin-based Matrix bot system.
# Copyright (C) 2018 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
import logging.config
import argparse
import asyncio
import signal
import copy
import sys

from .config import Config
from .db import init as init_db
from .server import MaubotServer
from .client import Client, init as init_client_class
from .loader.zip import init as init_zip_loader
from .instance import init as init_plugin_instance_class
from .management.api import init as init_management
from .__meta__ import __version__

parser = argparse.ArgumentParser(description="A plugin-based Matrix bot system.",
                                 prog="python -m maubot")
parser.add_argument("-c", "--config", type=str, default="config.yaml",
                    metavar="<path>", help="the path to your config file")
parser.add_argument("-b", "--base-config", type=str, default="example-config.yaml",
                    metavar="<path>", help="the path to the example config "
                                           "(for automatic config updates)")
args = parser.parse_args()

config = Config(args.config, args.base_config)
config.load()
config.update()

logging.config.dictConfig(copy.deepcopy(config["logging"]))
log = logging.getLogger("maubot.init")
log.info(f"Initializing maubot {__version__}")

loop = asyncio.get_event_loop()

init_zip_loader(config)
db_session = init_db(config)
clients = init_client_class(db_session, loop)
plugins = init_plugin_instance_class(db_session, config, loop)
management_api = init_management(config, loop)
server = MaubotServer(config, management_api, loop)

for plugin in plugins:
    plugin.load()

signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.default_int_handler)


async def periodic_commit():
    while True:
        await asyncio.sleep(60)
        db_session.commit()


periodic_commit_task: asyncio.Future = None

try:
    log.info("Starting server")
    loop.run_until_complete(server.start())
    log.info("Starting clients and plugins")
    loop.run_until_complete(asyncio.gather(*[client.start() for client in clients]))
    log.info("Startup actions complete, running forever")
    periodic_commit_task = asyncio.ensure_future(periodic_commit(), loop=loop)
    loop.run_forever()
except KeyboardInterrupt:
    log.debug("Interrupt received, stopping HTTP clients/servers and saving database")
    if periodic_commit_task is not None:
        periodic_commit_task.cancel()
    for client in Client.cache.values():
        client.stop()
    db_session.commit()
    loop.run_until_complete(server.stop())
    loop.close()
    log.debug("Everything stopped, shutting down")
    sys.exit(0)
