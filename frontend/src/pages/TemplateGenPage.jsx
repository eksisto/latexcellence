import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ArrowLeft, Sparkles } from "lucide-react";

export default function TemplateGenPage() {
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">
            Template Generator
          </h1>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Describe Your Template</CardTitle>
            <CardDescription>
              Generate a complete LaTeX template from a natural language
              description
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Textarea
              className="min-h-32 resize-none"
              placeholder={
                "Describe the template style you want, e.g.:\n" +
                "IEEE conference paper with two-column layout, abstract, " +
                "and bibliography..."
              }
            />
            <Button className="w-full">
              <Sparkles />
              Generate Template
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
