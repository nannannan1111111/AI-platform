export type JsonRecord = Record<string, any>;

export interface AdminBridge {
  api: (path: string, options?: RequestInit) => Promise<any>;
  toast: (message: string) => void;
  confirm: (message: string, title: string, actionLabel: string) => Promise<boolean>;
  navigate: (path: string) => void;
  navigateToLogin: () => void;
  invalidateSession: (message: string) => void;
  checkout: (checkout: JsonRecord) => void;
  authenticatedImage: (url: string) => Promise<string>;
  currentUser: JsonRecord | null;
  currentBalance: JsonRecord | null;
}

export interface AdminMountOptions {
  element: Element;
  route: string;
  bridge: AdminBridge;
}

declare global {
  interface Window {
    mountAdminVue?: (options: AdminMountOptions) => void;
    unmountAdminVue?: () => void;
  }
}
