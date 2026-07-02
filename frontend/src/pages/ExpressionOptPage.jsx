import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft } from "lucide-react";

export default function ExpressionOptPage() {
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">
            Expression Optimization
          </h1>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Optimize Expression</CardTitle>
            <CardDescription>
              AI suggests improvements to your academic writing style
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Open a project first to optimize expressions. You can customize
              the optimization prompt for your needs.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
