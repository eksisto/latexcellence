import { Link } from "react-router-dom";
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
    category: "项目管理",
    features: [
      {
        title: "项目工作区",
        description: "打开和编辑 LaTeX 项目",
        to: "/workspace",
        icon: FolderOpen,
        id: "workspace",
      },
    ],
  },
  {
    category: "内容导入",
    features: [
      {
        title: "公式 OCR 识别",
        description: "将图像转换为 LaTeX 公式",
        to: "/formula-ocr",
        icon: ScanText,
        id: "formula_ocr",
      },
      {
        title: "表格 OCR 识别",
        description: "将图像转换为 LaTeX 表格",
        to: "/table-ocr",
        icon: Table2,
        id: "table_ocr",
      },
      {
        title: "Markdown 转 LaTeX",
        description: "将 Markdown 文本转换为 LaTeX 代码",
        to: "/markdown-convert",
        icon: ArrowLeftRight,
        id: "markdown_converter",
      },
    ],
  },
  {
    category: "内容生成",
    features: [
      {
        title: "模版生成",
        description: "根据文字描述生成 LaTeX 模板",
        to: "/template-gen",
        icon: FileText,
        id: "template_gen",
      },
      {
        title: "参考文献管理",
        description: "识别参考文献格式，提取元数据并生成规范的 BibTeX 代码",
        to: "/bib-manager",
        icon: BookMarked,
        id: "bib_manager",
      },
    ],
  },
  {
    category: "文本审查与优化",
    features: [
      {
        title: "文本校对",
        description: "修复文档中的错别字、语法错误等",
        to: "/typo-check",
        icon: CheckCheck,
        id: "typo_check",
      },
      {
        title: "表达优化",
        description: "快速优化文本表达，提升文章质量",
        to: "/expression-opt",
        icon: PencilLine,
        id: "expression_opt",
      },
      {
        title: "术语一致性检查",
        description: "检测术语大小写、缩写、中英混用等一致性问题",
        to: "/terminology-check",
        icon: BookOpen,
        id: "terminology_check",
      },
    ],
  },
  {
    category: "格式化与排版",
    features: [
      {
        title: "源码格式化",
        description: "规范化间距、缩进与环境对齐，让 LaTeX 代码更易读",
        to: "/formatting",
        icon: AlignLeft,
        id: "formatting",
      },
      {
        title: "LaTeX 样式修改",
        description: "用自然语言描述样式修改，LLM 自动调整 LaTeX 代码",
        to: "/style-modify",
        icon: Wand2,
        id: "style_modify",
      },
    ],
  },
  {
    category: "版本控制",
    features: [
      {
        title: "Git 差异高亮",
        description: "将两个 Git 状态的文档差异编译为红删绿增 PDF",
        to: "/git-diff",
        icon: FileDiff,
        id: "git_diff",
      },
    ],
  },
  {
    category: "AI 辅助",
    features: [
      {
        title: "写作助手 Chat",
        description: "上下文感知的多轮对话，能直接修改指定段落",
        to: "/writing-chat",
        icon: MessagesSquare,
        id: "writing_chat",
      },
    ],
  },
];

export default function WelcomePage() {
  const enabledFeatures = useEnabledFeatures();

  const visibleGroups = featureGroups
    .map((group) => ({
      ...group,
      features: group.features.filter(
        (f) => !enabledFeatures || enabledFeatures[f.id]
      ),
    }))
    .filter((group) => group.features.length > 0);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-8">
      <h1 className="text-4xl font-bold mb-2 tracking-tight">LaTeXcellence</h1>
      <p className="text-muted-foreground mb-8 text-lg">
        让排版隐于无形，让思考回归本身
      </p>
      <div className="w-full space-y-8 max-w-[min(56rem,70%)]">
        {visibleGroups.map((group) => (
          <div key={group.category}>
            <h2 className="text-lg font-semibold mb-3 text-muted-foreground">
              {group.category}
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
      <div className="mt-4 text-sm text-muted-foreground">v0.1.0</div>
    </div>
  );
}
