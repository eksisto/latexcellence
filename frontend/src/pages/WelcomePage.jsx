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

const features = [
  {
    title: "Project Workspace",
    description: "Open and edit LaTeX projects",
    to: "/workspace",
    icon: FolderOpen,
  },
  {
    title: "Formula OCR",
    description: "Convert images to LaTeX formulas",
    to: "/formula-ocr",
    icon: ScanText,
  },
  {
    title: "Template Gen",
    description: "Generate LaTeX templates from descriptions",
    to: "/template-gen",
    icon: FileText,
  },
  {
    title: "Typo Check",
    description: "Find and fix typos in your document",
    to: "/typo-check",
    icon: CheckCheck,
  },
  {
    title: "Expression Optimize",
    description: "Improve academic writing style",
    to: "/expression-opt",
    icon: PencilLine,
  },
];

export default function WelcomePage() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-8">
      <h1 className="text-4xl font-bold mb-2 tracking-tight">LateXcellence</h1>
      <p className="text-muted-foreground mb-8 text-lg">
        AI-powered LaTeX document assistant
      </p>
      <div className="grid grid-cols-2 gap-4 max-w-lg w-full">
        {features.map(({ title, description, to, icon: Icon }) => (
          <Link key={to} to={to}>
            <Card className="h-full hover:scale-[1.02] hover:shadow-lg transition-all duration-300 ease-out cursor-pointer">
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
