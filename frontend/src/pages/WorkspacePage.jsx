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

export default function WorkspacePage() {
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
            Project Workspace
          </h1>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Welcome to the Workspace</CardTitle>
            <CardDescription>
              Open a LaTeX project to get started
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Project management and PDF preview will be available here.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
