import { useEffect } from "react";
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
import { api } from "@/api/index";
import useStore from "@/store/useStore";

const featuresList = [
  {
    title: "项目工作区",
    description: "打开和编辑 LaTeX 项目",
    to: "/workspace",
    icon: FolderOpen,
    id: "workspace",
  },
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
    title: "LaTeX 样式修改",
    description: "用自然语言描述样式修改，LLM 自动调整 LaTeX 代码",
    to: "/style-modify",
    icon: Wand2,
    id: "style_modify",
  },
  {
    title: "模版生成",
    description: "根据描述生成 LaTeX 模板",
    to: "/template-gen",
    icon: FileText,
    id: "template_gen",
  },
  {
    title: "文本校对",
    description: "修复文档中的错别字、语法错误等",
    to: "/typo-check",
    icon: CheckCheck,
    id: "typo_check",
  },
  {
    title: "表达优化",
    description: "改善写作风格",
    to: "/expression-opt",
    icon: PencilLine,
    id: "expression_opt",
  },
  {
    title: "源码格式化",
    description: "规范化间距、缩进与环境对齐",
    to: "/formatting",
    icon: AlignLeft,
    id: "formatting",
  },
  {
    title: "Git 差异高亮",
    description: "将两个 Git 状态的文档差异编译为红删绿增 PDF",
    to: "/git-diff",
    icon: FileDiff,
    id: "git_diff",
  },
  {
    title: "Markdown 转 LaTeX",
    description: "将 Markdown 文本转换为 LaTeX 代码",
    to: "/markdown-convert",
    icon: ArrowLeftRight,
    id: "markdown_converter",
  },
  {
    title: "术语一致性检查",
    description: "检测术语大小写、缩写、中英混用等一致性问题",
    to: "/terminology-check",
    icon: BookOpen,
    id: "terminology_check",
  },
  {
    title: "写作助手 Chat",
    description: "上下文感知的多轮对话，能直接修改指定段落",
    to: "/writing-chat",
    icon: MessagesSquare,
    id: "writing_chat",
  },
  {
    title: "参考文献管理",
    description: "识别参考文献格式，提取元数据并生成规范 BibTeX 代码",
    to: "/bib-manager",
    icon: BookMarked,
    id: "bib_manager",
  },
];

export default function WelcomePage() {
  const { enabledFeatures, setEnabledFeatures } = useStore();

  useEffect(() => {
    // 只有在未加载过时才获取（保存配置后会更新全局状态）
    if (enabledFeatures === null) {
      let mounted = true;
      api.configStatus()
        .then((data) => {
          if (mounted && data.features) {
            setEnabledFeatures({
              workspace: true, // 工作区永远启用
              formula_ocr: data.features.formula_ocr,
              table_ocr: data.features.table_ocr,
              template_gen: data.features.template_gen,
              style_modify: data.features.style_modify,
              typo_check: data.features.typo_check,
              expression_opt: data.features.expression_opt,
              formatting: data.features.formatting,
              compile: data.features.compile,
              git_diff: data.features.git_diff,
              markdown_converter: data.features.markdown_converter,
              terminology_check: data.features.terminology_check,
              writing_chat: data.features.writing_chat,
              bib_manager: data.features.bib_manager,
            });
          }
        })
        .catch(() => {
          if (mounted) {
            // 如果请求失败，默认全部显示
            setEnabledFeatures({
              workspace: true,
              formula_ocr: true,
              table_ocr: true,
              template_gen: true,
              style_modify: true,
              typo_check: true,
              expression_opt: true,
              formatting: true,
              compile: true,
              git_diff: true,
              markdown_converter: true,
              terminology_check: true,
              writing_chat: true,
              bib_manager: true,
            });
          }
        });

      return () => {
        mounted = false;
      };
    }
  }, [enabledFeatures, setEnabledFeatures]);

  const visibleFeatures = featuresList.filter(
    (f) => !enabledFeatures || enabledFeatures[f.id]
  );

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-8">
      <h1 className="text-4xl font-bold mb-2 tracking-tight">LaTeXcellence</h1>
      <p className="text-muted-foreground mb-8 text-lg">
        让排版隐于无形，让思考回归本身
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 max-w-7xl w-full">
        {visibleFeatures.map(({ title, description, to, icon: Icon }) => (
          <Link key={to} to={to}>
            <Card className="h-full transition-all duration-300 ease-out hover:scale-[1.02] hover:shadow-lg cursor-pointer">
              <CardHeader className="pb-2">
                <Icon className="size-5 text-muted-foreground mb-1" />
                <CardTitle className="text-base">{title}</CardTitle>
              </CardHeader>
              <CardContent>
                <CardDescription>{description}</CardDescription>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
      <Separator className="max-w-7xl w-full mt-8" />
      <div className="mt-4 text-sm text-muted-foreground">v0.1.0</div>
    </div>
  );
}
