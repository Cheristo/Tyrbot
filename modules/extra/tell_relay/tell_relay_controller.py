from core.command_param_types import Any
from core.decorators import instance, command
from core.setting_types import TextSettingType


@instance()
class TellRelayController:
    MESSAGE_SOURCE = "tell_relay"

    def inject(self, registry):
        self.bot = registry.get_instance("bot")
        self.text = registry.get_instance("text")
        self.pork_service = registry.get_instance("pork_service")
        self.setting_service = registry.get_instance("setting_service")
        self.character_service = registry.get_instance("character_service")
        self.public_channel_service = registry.get_instance("public_channel_service")
        self.message_hub_service = registry.get_instance("message_hub_service")
        self.ban_service = registry.get_instance("ban_service")

    def pre_start(self):
        self.message_hub_service.register_message_source(self.MESSAGE_SOURCE)

    def start(self):
        self.message_hub_service.register_message_destination(self.MESSAGE_SOURCE,
                                                              self.handle_incoming_relay_message,
                                                              ["private_channel", "org_channel", "discord", "websocket_relay", "shutdown_notice"],
                                                              [self.MESSAGE_SOURCE])

        self.setting_service.register(self.module_name, "relay_bot", "", TextSettingType(allow_empty=True), "Name of bot character for chat relay")

    def relay_bot(self):
        return self.setting_service.get("relay_bot")

    def relay_prefix(self):
        return self.setting_service.get("relay_prefix")

    @command(command="grc", params=[Any("message")], access_level="all",
             description="Accept incoming messages from relay bot")
    def grc_cmd(self, request, message):
        self.process_incoming_relay_message(request.sender, message)

    def process_incoming_relay_message(self, sender, message):
        relay_bot = self.relay_bot().get_value()
        if relay_bot and sender.name.lower() == relay_bot.lower():
            self.message_hub_service.send_message(self.MESSAGE_SOURCE, None, None, message)

    def handle_incoming_relay_message(self, ctx):
        message = ctx.formatted_message
        self.send_message_to_relay(message)

    def send_message_to_relay(self, message):
        relay_bot = self.relay_bot().get_value()
        if relay_bot:
            # if setting, then use setting, else if org, then use org name, else use botname
            prefix = self.get_org_channel_prefix()

            char_id = self.character_service.resolve_char_to_id(relay_bot)
            self.bot.send_private_message(char_id, "grc [%s] %s" % (prefix, message), add_color=False, conn=self.bot.get_primary_conn())

    def get_org_channel_prefix(self):
        conn = self.bot.get_primary_conn()
        return self.relay_prefix().get_value() or conn.get_org_name() or conn.get_char_name()
