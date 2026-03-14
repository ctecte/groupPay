interface TelegramWebApp {
  ready(): void;
  expand(): void;
  close(): void;
  initData: string;
  initDataUnsafe: {
    query_id?: string;
    user?: {
      id: number;
      first_name: string;
      last_name?: string;
      username?: string;
    };
    start_param?: string;
  };
  platform: string;
  version: string;
}

interface Window {
  Telegram?: {
    WebApp?: TelegramWebApp;
  };
}
