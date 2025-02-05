from core.ban_service import BanService
from core.chat_blob import ChatBlob
from core.command_param_types import Character
from core.decorators import instance, command, event
from core.dict_object import DictObject
from core.logger import Logger
from core.private_channel_service import PrivateChannelService
from core.setting_service import SettingService
from core.setting_types import TextSettingType
from core.standard_message import StandardMessage
from core.text import Text


@instance()
class PrivateChannelController:
    MESSAGE_SOURCE = "private_channel"

    def __init__(self):
        self.logger = Logger(__name__)
        self.private_channel_conn = None

    def inject(self, registry):
        self.bot = registry.get_instance("bot")
        self.private_channel_service = registry.get_instance("private_channel_service")
        self.character_service = registry.get_instance("character_service")
        self.job_scheduler = registry.get_instance("job_scheduler")
        self.access_service = registry.get_instance("access_service")
        self.message_hub_service = registry.get_instance("message_hub_service")
        self.ban_service = registry.get_instance("ban_service")
        self.log_controller = registry.get_instance("log_controller", is_optional=True)  # TODO core module depending on standard module
        self.online_controller = registry.get_instance("online_controller", is_optional=True)  # TODO core module depending on standard module
        self.text: Text = registry.get_instance("text")
        self.setting_service: SettingService = registry.get_instance("setting_service")

    def pre_start(self):
        self.message_hub_service.register_message_source(self.MESSAGE_SOURCE)

    def start(self):
        self.setting_service.register(self.module_name, "private_channel_prefix", "[Priv]", TextSettingType(["[Priv]", "[Guest]"]),
                                      "The name to show for messages coming from the private channel")

        self.setting_service.register(self.module_name, "private_channel_conn", "", TextSettingType(allow_empty=True),
                                      "The conn id or name to use for the private channel",
                                      extended_description="If empty, the bot will use the primary conn. You MUST restart the bot after changing this value for the change to take effect.")

        self.message_hub_service.register_message_destination(self.MESSAGE_SOURCE,
                                                              self.handle_incoming_relay_message,
                                                              ["org_channel", "discord", "websocket_relay", "broadcast", "raffle",
                                                               "shutdown_notice", "raid", "timers", "alliance"],
                                                              [self.MESSAGE_SOURCE])

    def handle_incoming_relay_message(self, ctx):
        self.bot.send_private_channel_message(ctx.formatted_message, conn=self.get_conn(None))

    @command(command="join", params=[], access_level="member",
             description="Join the private channel")
    def join_cmd(self, request):
        self.private_channel_service.invite(request.sender.char_id, self.get_conn(request.conn))

    @command(command="leave", params=[], access_level="all",
             description="Leave the private channel")
    def leave_cmd(self, request):
        self.private_channel_service.kick(request.sender.char_id, self.get_conn(request.conn))

    @command(command="invite", params=[Character("character")], access_level="all",
             description="Invite a character to the private channel")
    def invite_cmd(self, request, char):
        if char.char_id:
            conn = self.get_conn(request.conn)
            if char.char_id in conn.private_channel:
                return f"<highlight>{char.name}</highlight> is already in the private channel."
            else:
                self.bot.send_private_message(char.char_id,
                                              f"You have been invited to the private channel by <highlight>{request.sender.name}</highlight>.",
                                              conn=conn)
                self.private_channel_service.invite(char.char_id, conn)
                return f"You have invited <highlight>{char.name}</highlight> to the private channel."
        else:
            return StandardMessage.char_not_found(char.name)

    @command(command="kick", params=[Character("character")], access_level="moderator",
             description="Kick a character from the private channel")
    def kick_cmd(self, request, char):
        if char.char_id:
            conn = self.get_conn(request.conn)
            if char.char_id not in conn.private_channel:
                return f"<highlight>{char.name}</highlight> is not in the private channel."
            else:
                # TODO use request.sender.access_level and char.access_level
                if self.access_service.has_sufficient_access_level(request.sender.char_id, char.char_id):
                    self.bot.send_private_message(char.char_id,
                                                  f"You have been kicked from the private channel by <highlight>{request.sender.name}</highlight>.",
                                                  conn=conn)
                    self.private_channel_service.kick(char.char_id, conn)
                    return f"You have kicked <highlight>{char.name}</highlight> from the private channel."
                else:
                    return f"You do not have the required access level to kick <highlight>{char.name}</highlight>."
        else:
            return StandardMessage.char_not_found(char.name)

    @command(command="kickall", params=[], access_level="moderator",
             description="Kick all characters from the private channel")
    def kickall_cmd(self, request):
        conn = self.get_conn(request.conn)
        self.bot.send_private_channel_message(f"Everyone will be kicked from this channel in 10 seconds. [by <highlight>{request.sender.name}</highlight>]",
                                              conn=conn)
        self.job_scheduler.delayed_job(lambda t: self.private_channel_service.kickall(conn), 10)

    @event(event_type="connect", description="Load the conn ids as choice for private_channel_conn setting", is_system=True)
    def load_conns_into_setting_choice(self, event_type, event_data):
        options = []
        for _id, conn in self.bot.get_conns(lambda x: x.is_main == True):
            options.append(conn.char_name)

        setting = self.setting_service.get("private_channel_conn")
        setting.options = options

    @event(event_type=BanService.BAN_ADDED_EVENT, description="Kick characters from the private channel who are banned", is_system=True)
    def ban_added_event(self, event_type, event_data):
        self.private_channel_service.kick_from_all(event_data.char_id)

    @event(event_type=PrivateChannelService.PRIVATE_CHANNEL_MESSAGE_EVENT, description="Relay messages from the private channel to the relay hub", is_system=True)
    def handle_private_channel_message_event(self, event_type, event_data):
        if self.bot.get_conn_by_char_id(event_data.char_id) or self.ban_service.get_ban(event_data.char_id):
            return

        sender = DictObject({"char_id": event_data.char_id, "name": event_data.name})
        self.message_hub_service.send_message(self.MESSAGE_SOURCE, sender, self.get_private_channel_prefix(), event_data.message)

    @event(event_type=PrivateChannelService.JOINED_PRIVATE_CHANNEL_EVENT, description="Notify when a character joins the private channel")
    def handle_private_channel_joined_event(self, event_type, event_data):
        if self.online_controller:
            char_info = self.online_controller.get_char_info_display(event_data.char_id, event_data.conn)
        else:
            char_info = self.character_service.resolve_char_to_name(event_data.char_id)

        msg = f"{char_info} has joined the private channel."
        if self.log_controller:
            msg += " " + self.log_controller.get_logon(event_data.char_id)

        self.bot.send_private_channel_message(msg, conn=event_data.conn)
        self.message_hub_service.send_message(self.MESSAGE_SOURCE, None, self.get_private_channel_prefix(), msg)

    @event(event_type=PrivateChannelService.LEFT_PRIVATE_CHANNEL_EVENT, description="Notify when a character leaves the private channel")
    def handle_private_channel_left_event(self, event_type, event_data):
        msg = f"<highlight>{event_data.name}</highlight> has left the private channel."
        if self.log_controller:
            msg += " " + self.log_controller.get_logoff(event_data.char_id)

        self.bot.send_private_channel_message(msg, conn=event_data.conn)
        self.message_hub_service.send_message(self.MESSAGE_SOURCE, None, self.get_private_channel_prefix(), msg)

    @event(event_type=PrivateChannelService.PRIVATE_CHANNEL_COMMAND_EVENT, description="Relay commands from the private channel to the relay hub", is_system=True)
    def outgoing_private_channel_message_event(self, event_type, event_data):
        sender = None
        if event_data.name:
            sender = DictObject({"char_id": event_data.char_id, "name": event_data.name})

        if isinstance(event_data.message, ChatBlob):
            pages = self.text.paginate(ChatBlob(event_data.message.title, event_data.message.msg),
                                       event_data.conn,
                                       self.setting_service.get("org_channel_max_page_length").get_value())
            if len(pages) < 4:
                for page in pages:
                    self.message_hub_service.send_message(self.MESSAGE_SOURCE, sender, self.get_private_channel_prefix(), page)
            else:
                self.message_hub_service.send_message(self.MESSAGE_SOURCE, sender, self.get_private_channel_prefix(), event_data.message.title)
        else:
            self.message_hub_service.send_message(self.MESSAGE_SOURCE, sender, self.get_private_channel_prefix(), event_data.message)

    def get_conn(self, conn):
        if self.private_channel_conn:
            return self.private_channel_conn

        conn_id = self.setting_service.get_value("private_channel_conn")
        if conn_id:
            for _id, conn in self.bot.get_conns(lambda x: x.id == conn_id or x.char_name == conn_id):
                self.private_channel_conn = conn
                break

            if not self.private_channel_conn:
                self.logger.warning(f"Could not find conn with id '{conn_id}', defaulting to primary conn")
                self.private_channel_conn = self.bot.get_primary_conn()
        else:
            # use the primary conn if private_channel_conn is not set
            self.private_channel_conn = self.bot.get_primary_conn()

        return self.private_channel_conn

    def get_private_channel_prefix(self):
        return self.setting_service.get_value("private_channel_prefix")
