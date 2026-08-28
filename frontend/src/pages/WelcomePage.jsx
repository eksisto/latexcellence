import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import FolderOpen from "lucide-react/dist/esm/icons/folder-open.mjs";
import FileCode2 from "lucide-react/dist/esm/icons/file-code-2.mjs";
import { Button } from "@/components/ui/button";
import Wordmark from "@/components/Wordmark";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";
import { isMultiUser } from "@/api/connection";
import { SECTION_BY_GROUP } from "@/lib/writingFlow";
import ScanText from "lucide-react/dist/esm/icons/scan-text.mjs";
import Table2 from "lucide-react/dist/esm/icons/table-2.mjs";
import Scroll from "lucide-react/dist/esm/icons/scroll.mjs";
import SpellCheck from "lucide-react/dist/esm/icons/spell-check.mjs";
import PencilSparkles from "lucide-react/dist/esm/icons/pencil-sparkles.mjs";
import ListChecks from "lucide-react/dist/esm/icons/list-checks.mjs";
import ArrowLeftRight from "lucide-react/dist/esm/icons/arrow-left-right.mjs";
import FileDiff from "lucide-react/dist/esm/icons/file-diff.mjs";
import History from "lucide-react/dist/esm/icons/history.mjs";
import PencilRuler from "lucide-react/dist/esm/icons/pencil-ruler.mjs";
import FileTypeCorner from "lucide-react/dist/esm/icons/file-type-corner.mjs";
import MessagesSquare from "lucide-react/dist/esm/icons/messages-square.mjs";
import Album from "lucide-react/dist/esm/icons/album.mjs";
import Languages from "lucide-react/dist/esm/icons/languages.mjs";
import ChartSpline from "lucide-react/dist/esm/icons/chart-spline.mjs";

const featureGroups = [
  {
    category: "contentImport",
    features: [
      {
        titleKey: "formulaOcr.title",
        to: "/formula-ocr",
        icon: ScanText,
        id: "formula_ocr",
      },
      {
        titleKey: "tableOcr.title",
        to: "/table-ocr",
        icon: Table2,
        id: "table_ocr",
      },
      {
        titleKey: "formatConvert.title",
        to: "/format-convert",
        icon: ArrowLeftRight,
        id: "format_converter",
      },
    ],
  },
  {
    category: "contentGeneration",
    features: [
      {
        titleKey: "templateGen.title",
        to: "/template-gen",
        icon: Scroll,
        id: "template_gen",
      },
      {
        titleKey: "plotGen.title",
        to: "/plot-gen",
        icon: ChartSpline,
        id: "plot_gen",
      },
      {
        titleKey: "bibManager.title",
        to: "/bib-manager",
        icon: Album,
        id: "bib_manager",
      },
    ],
  },
  {
    category: "textReview",
    features: [
      {
        titleKey: "typoCheck.title",
        to: "/typo-check",
        icon: SpellCheck,
        id: "typo_check",
      },
      {
        titleKey: "expressionOpt.title",
        to: "/expression-opt",
        icon: PencilSparkles,
        id: "expression_opt",
      },
      {
        titleKey: "terminologyCheck.title",
        to: "/terminology-check",
        icon: FileTypeCorner,
        id: "terminology_check",
      },
      {
        titleKey: "paragraphTranslate.title",
        to: "/paragraph-translate",
        icon: Languages,
        id: "paragraph_translate",
      },
    ],
  },
  {
    category: "code_review",
    features: [
      {
        titleKey: "code_review.title",
        to: "/code-review",
        icon: ListChecks,
        id: "code_review",
      },
      {
        titleKey: "styleModify.title",
        to: "/style-modify",
        icon: PencilRuler,
        id: "style_modify",
      },
    ],
  },
  {
    category: "versionControl",
    features: [
      {
        titleKey: "gitDiff.title",
        to: "/git-diff",
        icon: FileDiff,
        id: "git_diff",
      },
      {
        titleKey: "versionManage.title",
        to: "/versions",
        icon: History,
        id: "git_manage",
      },
    ],
  },
  {
    category: "aiAssist",
    features: [
      {
        titleKey: "writingChat.title",
        to: "/writing-chat",
        icon: MessagesSquare,
        id: "writing_chat",
      },
      {
        titleKey: "editor.title",
        to: "/editor",
        icon: FileCode2,
        id: "editor",
      },
    ],
  },
];

export default function WelcomePage() {
  const { t } = useTranslation(["pages", "common"]);
  const enabledFeatures = useEnabledFeatures();

  const visibleGroups = useMemo(() => {
    return featureGroups
      .map((group) => ({
        ...group,
        sectionNo: SECTION_BY_GROUP[group.category],
        categoryLabel: t(`pages:welcome.groups.${group.category}`),
        features: group.features
          .filter((f) => !enabledFeatures || enabledFeatures[f.id])
          .map((f) => ({
            ...f,
            title: t(`pages:welcome.features.${f.titleKey}`),
          })),
      }))
      .filter((group) => group.features.length > 0);
  }, [t, enabledFeatures]);

  return (
    <div className="min-h-screen animate-tab-enter">
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-12 md:px-10 md:py-16">
        {/* 标题页 + 点线目录 */}
        <header className="grid flex-1 items-center gap-12 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-16">
          {/* 标题块 */}
          <div className="max-w-md">
            <h1
              className="reveal text-[2.6rem] font-medium leading-tight tracking-[-0.01em] md:text-6xl"
              style={{ animationDelay: "0ms" }}
            >
              <Wordmark />
            </h1>
            <p
              className="reveal mt-6 text-lg leading-relaxed text-muted-foreground"
              style={{ animationDelay: "90ms" }}
            >
              {t("pages:welcome.slogan")}
            </p>
            <div className="reveal mt-8" style={{ animationDelay: "180ms" }}>
              <Button
                size="lg"
                nativeButton={false}
                render={
                  <Link to={isMultiUser() ? "/projects" : "/workspace"} />
                }
              >
                <FolderOpen />
                {isMultiUser()
                  ? t("common:openProjects")
                  : t("common:openWorkspace")}
              </Button>
            </div>
          </div>

          {/* 点线目录：功能条目按「写作流程」章节分组 */}
          <nav
            aria-label={t("pages:welcome.tocLabel")}
            className="reveal"
            style={{ animationDelay: "260ms" }}
          >
            <div className="space-y-7">
              {visibleGroups.map((group, gi) => (
                <section
                  key={group.category}
                  className="reveal"
                  style={{ animationDelay: `${300 + gi * 70}ms` }}
                >
                  <h2 className="mb-1 flex items-baseline gap-3">
                    <span className="-translate-y-0.5 font-display text-sm font-semibold text-muted-foreground">
                      §{group.sectionNo}
                    </span>
                    <span className="font-heading text-base font-semibold text-foreground/80">
                      {group.categoryLabel}
                    </span>
                    <span
                      className="flex-1 border-b border-border"
                      aria-hidden="true"
                    />
                  </h2>
                  <ul>
                    {group.features.map(({ id, title, to, icon: Icon }) => (
                      <li key={id}>
                        <Link
                          to={to}
                          className="group flex items-center gap-3 rounded-md py-[7px] pl-3 pr-2 transition-colors duration-200 hover:bg-accent/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                        >
                          <span className="text-[15px] leading-snug">
                            {title}
                          </span>
                          <span
                            className="flex-1 border-b border-dotted border-foreground/25 transition-colors duration-200 group-hover:border-foreground/50"
                            aria-hidden="true"
                          />
                          <Icon className="size-4 shrink-0 text-muted-foreground transition-colors duration-200 group-hover:text-foreground" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          </nav>
        </header>

        {/* colophon：字标 + 版本，像文档版权页 */}
        <footer className="mt-16 flex items-center justify-between border-t border-border pt-6">
          <Wordmark className="text-sm opacity-60" />
          <span className="font-mono text-xs text-muted-foreground">
            {t("pages:welcome.version")}
          </span>
        </footer>
      </div>
    </div>
  );
}
