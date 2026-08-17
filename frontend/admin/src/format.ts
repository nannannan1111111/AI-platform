export function formatCredits(value: unknown = "0.00"): string {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "0.00";
}

export function formatBytes(value: unknown = 0): string {
  const bytes = Math.max(Number(value) || 0, 0);
  if (bytes < 1000) return `${Math.round(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB", "PB", "EB"];
  let amount = bytes;
  let index = -1;
  do {
    amount /= 1000;
    index += 1;
  } while (amount >= 1000 && index < units.length - 1);
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: amount >= 100 ? 0 : 2 }).format(amount)} ${units[index]}`;
}

export function formatDate(value: unknown): string {
  if (!value) return "—";
  const parsed = new Date(String(value));
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请稍后重试";
}

export function localDateTimeValue(date: Date): string {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}
