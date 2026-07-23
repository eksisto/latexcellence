/**
 * Writing Chat 验证码处理逻辑。
 */

import { useState, useCallback } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import i18n from "@/i18n";
import { translateError } from "@/utils/errorMessages";

/**
 * 创建验证码处理相关的 hooks 和回调。
 */
export function useWritingChatCaptcha({ setError }) {
  const [captchaChallenge, setCaptchaChallenge] = useState(null);
  const [captchaSubmitting, setCaptchaSubmitting] = useState(false);

  const submitCaptcha = useCallback(
    async (selectedHashes) => {
      if (!captchaChallenge || captchaSubmitting) return;
      setCaptchaSubmitting(true);
      try {
        const result = await api.writingChat.submitDDGChallenge(
          captchaChallenge.challenge_id,
          selectedHashes,
        );
        if (result.success) {
          toast.success(i18n.t("hooks:writingChat.captchaSuccess"), {
            description: i18n.t("hooks:writingChat.captchaSuccessDesc"),
          });
        } else {
          toast.error(i18n.t("hooks:writingChat.captchaFailed"), {
            description: translateError(result.message) || i18n.t("hooks:writingChat.captchaFailedHint"),
          });
        }
        setCaptchaChallenge(null);
      } catch (e) {
        setError(e.message);
        toast.error(i18n.t("hooks:writingChat.submitCaptchaFailed"), { description: e.message });
      } finally {
        setCaptchaSubmitting(false);
      }
    },
    [captchaChallenge, captchaSubmitting, setError]
  );

  const dismissCaptcha = useCallback(() => {
    setCaptchaChallenge(null);
  }, []);

  return {
    captchaChallenge,
    setCaptchaChallenge,
    captchaSubmitting,
    submitCaptcha,
    dismissCaptcha,
  };
}
