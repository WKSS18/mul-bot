export type ID = string;

export interface ApiEnvelope<T> {
  data: T;
  request_id: string;
}

export interface RoomSummary {
  id: ID;
  name: string;
  revision: number;
  archived: boolean;
}

export interface Mention {
  bot_id: ID;
  position: number;
}

export interface SendMessageRequest {
  client_message_id: ID;
  text: string;
  mentions: Mention[];
  attachment_ids: ID[];
}

export interface SendMessageResponse {
  message_id: ID;
  message_seq: number;
  run_id: ID | null;
  run_status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | null;
}

export interface FileCard {
  id: ID;
  filename: string;
  mime: string;
  parse_state: 'PENDING' | 'PROCESSING' | 'READY' | 'FAILED' | 'UNSUPPORTED';
}

export interface ChatMessage {
  id: ID;
  message_seq: number;
  sender_id: ID | null;
  sender_type: 'USER' | 'BOT';
  bot_id: ID | null;
  body: string;
  created_at: string;
  attachments: FileCard[];
}

export type RoomEventType =
  | 'message.created'
  | 'message.delta'
  | 'message.completed'
  | 'run.step.started'
  | 'run.completed'
  | 'run.failed'
  | 'tool.completed';

export interface RoomEvent<T = Record<string, unknown>> {
  type: RoomEventType;
  room_id: ID;
  seq: number;
  payload: T;
}

export interface AuthCommand {type: 'auth'; ticket: string}
export interface SubscribeCommand {type: 'subscribe'; room_id: ID; after_seq: number}
export interface PingCommand {type: 'ping'}
export type RealtimeCommand = AuthCommand | SubscribeCommand | PingCommand;
