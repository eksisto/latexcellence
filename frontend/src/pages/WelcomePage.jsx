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
  FileText,
  CheckCheck,
  PencilLine,
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
    title: "模版生成",
    description: "根据描述生成 LaTeX 模板",
    to: "/template-gen",
    icon: FileText,
    id: "template_gen",
  },
  {
    title: "文本校对",
    description: "发现并修复文档中的错别字、语法错误等",
    to: "/typo-check",
    icon: CheckCheck,
    id: "typo_check",
  },
  {
    title: "表达优化",
    description: "改善学术写作风格",
    to: "/expression-opt",
    icon: PencilLine,
    id: "expression_opt",
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
              template_gen: data.features.template_gen,
              typo_check: data.features.typo_check,
              expression_opt: data.features.expression_opt,
            });
          }
        })
        .catch(() => {
          if (mounted) {
            // 如果请求失败，默认全部显示
            setEnabledFeatures({
              workspace: true,
              formula_ocr: true,
              template_gen: true,
              typo_check: true,
              expression_opt: true,
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
        AI 驱动的 LaTeX 文档助手
      </p>
      <div className="grid grid-cols-2 gap-4 max-w-lg w-full">
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
      <Separator className="max-w-lg w-full mt-8" />
      <div className="mt-4 text-sm text-muted-foreground">v0.1.0</div>
    </div>
  );
}
