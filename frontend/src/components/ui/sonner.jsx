import { Toaster as SonnerToaster } from "sonner";
import { useTheme } from "@/hooks/useTheme";

function Toaster({
  position = "bottom-right",
  richColors = true,
  closeButton = true,
  duration = 4000,
  expand = true,
  ...props
}) {
  const { resolved } = useTheme();

  return (
    <SonnerToaster
      theme={resolved}
      position={position}
      richColors={richColors}
      closeButton={closeButton}
      duration={duration}
      expand={expand}
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-popover group-[.toaster]:text-popover-foreground group-[.toaster]:border-border group-[.toaster]:ring-1 group-[.toaster]:ring-foreground/10 group-[.toaster]:rounded-xl",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
}

export { Toaster };
