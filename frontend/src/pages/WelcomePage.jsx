import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  FolderOpen,
  ScanText,
  Table2,
  FileText,
  CheckCheck,
  PencilLine,
  AlignLeft,
  ArrowLeftRight,
  FileDiff,
  Wand2,
  BookOpen,
  MessagesSquare,
  BookMarked,
} from "lucide-react";
import useEnabledFeatures from "@/hooks/useEnabledFeatures";

const featureGroups = [
  {
    category: "groups.projectManagement",
    features: [
      {
        titleKey: "workspace.title",
        descKey: "workspace.desc",
        to: "/workspace",
        icon: FolderOpen,
        id: "workspace",
      },
    ],
  },
  {
    category: "groups.contentImport",
    features: [
      {
        titleKey: "formulaOcr.title",
        descKey: "formulaOcr.desc",
        to: "/formula-ocr",
        icon: ScanText,
        id: "formula_ocr",
      },
      {
        titleKey: "tableOcr.title",
        descKey: "tableOcr.desc",
        to: "/table-ocr",
        icon: Table2,
        id: "table_ocr",
      },
      {
        titleKey: "markdownConvert.title",
        descKey: "markdownConvert.desc",
        to: "/markdown-convert",
        icon: ArrowLeftRight,
        id: "markdown_converter",
      },
    ],
  },
  {
    category: "groups.contentGeneration",
    features: [
      {
        titleKey: "templateGen.title",
        descKey: "templateGen.desc",
        to: "/template-gen",
        icon: FileText,
        id: "template_gen",
      },
      {
        titleKey: "bibManager.title",
        descKey: "bibManager.desc",
        to: "/bib-manager",
        icon: BookMarked,
        id: "bib_manager",
      },
    ],
  },
  {
    category: "groups.textReview",
    features: [
      {
        titleKey: "typoCheck.title",
        descKey: "typoCheck.desc",
        to: "/typo-check",
        icon: CheckCheck,
        id: "typo_check",
      },
      {
        titleKey: "expressionOpt.title",
        descKey: "expressionOpt.desc",
        to: "/expression-opt",
        icon: PencilLine,
        id: "expression_opt",
      },
      {
        titleKey: "terminologyCheck.title",
        descKey: "terminologyCheck.desc",
        to: "/terminology-check",
        icon: BookOpen,
        id: "terminology_check",
      },
    ],
  },
  {
    category: "groups.formatting",
    features: [
      {
        titleKey: "formatting.title",
        descKey: "formatting.desc",
        to: "/formatting",
        icon: AlignLeft,
        id: "formatting",
      },
      {
        titleKey: "styleModify.title",
        descKey: "styleModify.desc",
        to: "/style-modify",
        icon: Wand2,
        id: "style_modify",
      },
    ],
  },
  {
    category: "groups.versionControl",
    features: [
      {
        titleKey: "gitDiff.title",
        descKey: "gitDiff.desc",
        to: "/git-diff",
        icon: FileDiff,
        id: "git_diff",
      },
    ],
  },
  {
    category: "groups.aiAssist",
    features: [
      {
        titleKey: "writingChat.title",
        descKey: "writingChat.desc",
        to: "/writing-chat",
        icon: MessagesSquare,
        id: "writing_chat",
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
        categoryLabel: t(`pages:welcome.${group.category}`),
        features: group.features
          .filter((f) => !enabledFeatures || enabledFeatures[f.id])
          .map((f) => ({
            ...f,
            title: t(`pages:welcome.features.${f.titleKey}`),
            description: t(`pages:welcome.features.${f.descKey}`),
          })),
      }))
      .filter((group) => group.features.length > 0);
  }, [t, enabledFeatures]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-8">
      <h1 className="text-4xl font-bold mb-2 tracking-tight">LaTeXcellence</h1>
      <p className="text-muted-foreground mb-8 text-lg">
        {t("pages:welcome.slogan")}
      </p>
      <div className="w-full space-y-8 max-w-[min(56rem,70%)]">
        {visibleGroups.map((group) => (
          <div key={group.category}>
            <h2 className="text-lg font-semibold mb-3 text-muted-foreground">
              {group.categoryLabel}
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-5 gap-y-4">
              {group.features.map(({ title, description, to, icon: Icon }) => (
                <Link key={to} to={to}>
                  <Card className="h-full transition-all duration-300 ease-out hover:scale-[1.02] hover:shadow-lg cursor-pointer">
                    <CardHeader className="pb-2 flex flex-row items-center gap-2">
                      <Icon className="size-5 text-muted-foreground shrink-0" />
                      <CardTitle className="text-base">{title}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <CardDescription className="mx-0.5">{description}</CardDescription>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          </div>
        ))}
      </div>
      <Separator className="max-w-7xl w-full mt-8" />
      <div className="mt-4 text-sm text-muted-foreground">{t("pages:welcome.version")}</div>
    </div>
  );
}
