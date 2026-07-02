import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft, Upload } from "lucide-react";

export default function FormulaOCRPage() {
  return (
    <div className="min-h-screen p-8">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/">
              <ArrowLeft />
            </Link>
          </Button>
          <h1 className="text-3xl font-bold tracking-tight">Formula OCR</h1>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Upload Formula Image</CardTitle>
            <CardDescription>
              Convert handwritten or printed math formulas to LaTeX code
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="border-2 border-dashed border-border rounded-lg p-12 text-center cursor-pointer hover:border-ring/50 hover:bg-muted/50 transition-colors">
              <Upload className="size-10 mx-auto mb-3 text-muted-foreground" />
              <p className="text-muted-foreground">
                Click or drag an image to upload
              </p>
              <p className="text-muted-foreground/60 text-sm mt-1">
                PNG, JPEG, WebP up to 10MB
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
