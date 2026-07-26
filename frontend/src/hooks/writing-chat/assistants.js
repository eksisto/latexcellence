/**
 * Writing Chat 助手管理逻辑。
 */

import { useState, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { api } from "@/api/index";
import i18n from "@/i18n";

/**
 * 创建助手管理相关的 hooks 和回调。
 */
export function useWritingChatAssistants() {
  const [assistants, setAssistants] = useState([]);
  const [activeAssistant, setActiveAssistant] = useState("");
  const [assistantContent, setAssistantContent] = useState(null);

  // ── 加载助手列表 ─────────────────────────────────────────────────
  const refreshAssistants = useCallback(async () => {
    try {
      const data = await api.writingChat.assistants.list();
      setAssistants(data.prompts || []);
      const active = (data.prompts || []).find((p) => p.active);
      if (active) {
        setActiveAssistant(active.name);
        try {
          const d = await api.writingChat.assistants.load(active.name);
          setAssistantContent(d.content || "");
        } catch {
          setAssistantContent("");
        }
      } else {
        setActiveAssistant("");
        setAssistantContent(null);
      }
    } catch {
      /* silent */
    }
  }, []);

  useEffect(() => {
    refreshAssistants();
  }, [refreshAssistants]);

  // ── 助手提示词管理 ─────────────────────────────────────────────────
  const setAssistantActive = useCallback(
    async (name) => {
      if (name === activeAssistant) {
        // 取消激活
        try {
          await api.writingChat.assistants.setActive("");
        } catch {}
        setActiveAssistant("");
        setAssistantContent(null);
        return;
      }
      try {
        await api.writingChat.assistants.setActive(name);
        setActiveAssistant(name);
        const d = await api.writingChat.assistants.load(name);
        setAssistantContent(d.content || "");
      } catch (e) {
        toast.error(i18n.t("hooks:writingChat.switchAssistantFailed"), {
          description: e.message,
        });
      }
    },
    [activeAssistant],
  );

  const saveAssistant = useCallback(
    async (name, content) => {
      if (!name?.trim() || !content?.trim()) return;
      try {
        await api.writingChat.assistants.save(name, content);
        await refreshAssistants();
        toast.success(i18n.t("hooks:writingChat.assistantSaved"));
      } catch (e) {
        toast.error(i18n.t("hooks:writingChat.saveAssistantFailed"), {
          description: e.message,
        });
      }
    },
    [refreshAssistants],
  );

  const deleteAssistant = useCallback(
    async (name) => {
      try {
        await api.writingChat.assistants.delete(name);
        if (activeAssistant === name) {
          setActiveAssistant("");
          setAssistantContent(null);
        }
        await refreshAssistants();
      } catch (e) {
        toast.error(i18n.t("hooks:writingChat.deleteAssistantFailed"), {
          description: e.message,
        });
      }
    },
    [activeAssistant, refreshAssistants],
  );

  return {
    assistants,
    activeAssistant,
    assistantContent,
    setAssistantActive,
    saveAssistant,
    deleteAssistant,
    refreshAssistants,
  };
}
