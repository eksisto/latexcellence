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

export default function TypoCheckPage() {
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">Typo Check</h1>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Check for Typos</CardTitle>
            <CardDescription>
              AI scans your LaTeX document for spelling and grammar errors
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Open a project first to check for typos. Paragraphs will be
              scanned by AI and reviewed by you.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
