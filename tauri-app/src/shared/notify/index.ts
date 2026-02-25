import type { ToastType } from "@/components/ui/toast";
import { useUiStore } from "@/stores/uiStore";

export function notify(type: ToastType, message: string) {
  useUiStore.getState().addToast(type, message);
}
