import { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  checkConnection,
  getDefaultApiBase,
  getApiBase,
  getApiToken,
  setApiConnection,
} from "@/api/connection";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * 后端连接面板：编辑 API 基地址 / 访问令牌并测试连接。
 *
 * 仅在设置对话框打开时自动探测一次连接状态；open 为 false 时不发起请求，
 * 用户在面板中修改地址 / 令牌后会丢弃过期的自动探测结果。
 *
 * 传入 onSaved 时切换为「认证页改绑后端」形态：不再失焦静默持久化，
 * 改为显示「保存并刷新」按钮，由调用方决定保存后的行为（如刷新页面重新探测）。
 */
function ConnectionPanel({ open = true, onSaved }) {
  const defaultApiBase = getDefaultApiBase();
  const [apiBase, setApiBase] = useState(getApiBase);
  const [apiToken, setApiToken] = useState(getApiToken);
  const [testing, setTesting] = useState(false);
  const [connectionResult, setConnectionResult] = useState(null);
  const { t } = useTranslation("components");
  const tRef = useRef(t);
  tRef.current = t;

  useEffect(() => {
    if (!open) return;
    setApiBase(getApiBase());
    setApiToken(getApiToken());
    setConnectionResult(null);

    // 自动探测一次后端连接，刷新连接状态；
    // 若用户随后修改了地址/令牌，则丢弃过期结果
    const base = getApiBase();
    const token = getApiToken();
    const controller = new AbortController();
    checkConnection(base, token, controller.signal)
      .then((info) => {
        if (getApiBase() === base && getApiToken() === token) {
          setConnectionResult({
            ok: true,
            message: tRef.current("settings.connectionOk", {
              model: info.model,
            }),
          });
        }
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        if (getApiBase() === base && getApiToken() === token) {
          setConnectionResult({ ok: false, message: err.message });
        }
      });

    return () => controller.abort();
  }, [open]);

  function handleApiBaseChange(value) {
    setApiBase(value);
    setConnectionResult(null);
  }

  function handleApiTokenChange(value) {
    setApiToken(value);
    setConnectionResult(null);
  }

  /** 失焦时统一持久化，避免每个按键都同步写 localStorage；
   *  onSaved 形态下改为显式按钮保存，失焦不落盘 */
  function persistConnection() {
    if (onSaved) return;
    setApiConnection(apiBase, apiToken);
  }

  async function handleTestConnection() {
    setTesting(true);
    setConnectionResult(null);
    persistConnection();
    try {
      const info = await checkConnection(apiBase, apiToken);
      setConnectionResult({
        ok: true,
        message: t("settings.connectionOk", { model: info.model }),
      });
    } catch (err) {
      setConnectionResult({ ok: false, message: err.message });
    } finally {
      setTesting(false);
    }
  }

  const state = testing
    ? {
        label: t("settings.testing"),
        dot: "bg-amber-500",
        text: "text-amber-600",
      }
    : connectionResult
      ? connectionResult.ok
        ? {
            label: t("settings.connected"),
            dot: "bg-emerald-500",
            text: "text-emerald-600",
          }
        : {
            label: t("settings.connectionFailed"),
            dot: "bg-destructive",
            text: "text-destructive",
          }
      : {
          label: t("settings.notTested"),
          dot: "bg-muted-foreground/40",
          text: "text-muted-foreground",
        };

  return (
    <div className="rounded-lg border border-border bg-muted/40 p-3">
      <div className="flex items-center justify-between gap-2 pb-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("settings.connection")}
        </span>
        <span className={`flex items-center gap-1.5 text-xs ${state.text}`}>
          <span
            className={`size-2 rounded-full ${state.dot}`}
            aria-hidden="true"
          />
          {state.label}
        </span>
      </div>
      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.apiBase")}</Label>
          <Input
            placeholder={defaultApiBase}
            value={apiBase}
            onChange={(e) => handleApiBaseChange(e.target.value)}
            onBlur={persistConnection}
          />
          <p className="text-xs text-muted-foreground">
            {t("settings.apiBaseHint", { default: defaultApiBase })}
          </p>
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t("settings.apiToken")}</Label>
          <Input
            type="password"
            placeholder={t("settings.apiTokenPlaceholder")}
            value={apiToken}
            onChange={(e) => handleApiTokenChange(e.target.value)}
            onBlur={persistConnection}
          />
          <p className="text-xs text-muted-foreground">
            {t("settings.apiTokenHint")}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {connectionResult && (
            <span
              className={`min-w-0 flex-1 truncate text-xs ${
                connectionResult.ok ? "text-emerald-600" : "text-destructive"
              }`}
            >
              {connectionResult.message}
            </span>
          )}
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTestConnection}
              disabled={testing || !apiBase.trim()}
            >
              {testing ? t("settings.testing") : t("settings.testConnection")}
            </Button>
            {onSaved && (
              <Button
                size="sm"
                disabled={testing || !apiBase.trim()}
                onClick={() => onSaved(apiBase.trim(), apiToken)}
              >
                {t("settings.saveAndReload")}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default memo(ConnectionPanel);
