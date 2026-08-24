import { createApp, type App as VueApp, defineComponent, h } from "vue";

import AccountPage from "./pages/AccountPage.vue";
import AssetsPage from "./pages/AssetsPage.vue";
import GenerationCapacityPage from "./pages/GenerationCapacityPage.vue";
import GenerationsPage from "./pages/GenerationsPage.vue";
import GenerationTasksPage from "./pages/GenerationTasksPage.vue";
import PlatformContentPage from "./pages/PlatformContentPage.vue";
import EmailSettingsPage from "./pages/EmailSettingsPage.vue";
import ModelRoutingPage from "./pages/ModelRoutingPage.vue";
import PaymentSettingsPage from "./pages/PaymentSettingsPage.vue";
import ProviderCostsPage from "./pages/ProviderCostsPage.vue";
import RechargePackagesPage from "./pages/RechargePackagesPage.vue";
import RunningHubCapabilitiesPage from "./pages/RunningHubCapabilitiesPage.vue";
import LLMSettingsPage from "./pages/LLMSettingsPage.vue";
import ModelsPage from "./pages/ModelsPage.vue";
import StorageAllowancePage from "./pages/StorageAllowancePage.vue";
import UsersPage from "./pages/UsersPage.vue";
import WalletPage from "./pages/WalletPage.vue";
import RedeemCodesPage from "./pages/RedeemCodesPage.vue";
import type { AdminMountOptions } from "./types";

const pages = {
  "/admin/users": UsersPage,
  "/admin/generation-tasks": GenerationTasksPage,
  "/admin/storage-allowance": StorageAllowancePage,
  "/admin/generation-capacity": GenerationCapacityPage,
  "/admin/email-settings": EmailSettingsPage,
  "/admin/platform-content": PlatformContentPage,
  "/admin/model-routing": ModelRoutingPage,
  "/admin/provider-costs": ProviderCostsPage,
  "/admin/recharge-packages": RechargePackagesPage,
  "/admin/payment-settings": PaymentSettingsPage,
  "/admin/runninghub-capabilities": RunningHubCapabilitiesPage,
  "/workspace/account": AccountPage,
  "/workspace/wallet": WalletPage,
  "/admin/redeem-codes": RedeemCodesPage,
  "/workspace/generations": GenerationsPage,
  "/workspace/models": ModelsPage,
  "/workspace/assets": AssetsPage,
  "/workspace/llm-settings": LLMSettingsPage,
} as const;

let mountedApp: VueApp<Element> | null = null;

window.unmountAdminVue = () => {
  mountedApp?.unmount();
  mountedApp = null;
};

window.mountAdminVue = ({ element, route, bridge }: AdminMountOptions) => {
  window.unmountAdminVue?.();
  const Page = pages[route as keyof typeof pages];
  if (!Page) throw new Error(`没有可用于 ${route} 的 Vue 管理页面`);
  const Root = defineComponent({
    name: "AdminVueRoot",
    setup: () => () => h(Page, { bridge }),
  });
  mountedApp = createApp(Root);
  mountedApp.mount(element);
};

window.dispatchEvent(new Event("admin-vue-ready"));
