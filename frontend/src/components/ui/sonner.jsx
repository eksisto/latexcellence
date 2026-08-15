import { Toaster as SonnerToaster } from "sonner";
import { X } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";

function SolidCheck({ className }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" fill="currentColor" />
      <path
        d="m8.2 12.3 2.6 2.6 5-5.3"
        stroke="var(--popover)"
        strokeWidth="2.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SolidInfo({ className }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="12" fill="currentColor" />
      <circle cx="12.1" cy="7.3" r="1.6" fill="var(--popover)" />
      <path
        fill="var(--popover)"
        d="M13.3 12.2V18c0 .7-.6 1.2-1.2 1.2s-1.2-.6-1.2-1.2v-5.2H10c-.7 0-1.2-.6-1.2-1.2s.6-1.2 1.2-1.2h1.5c1-.1 1.9.8 1.8 1.8z"
      />
    </svg>
  );
}

function SolidWarning({ className }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M12 3.6 21.3 19.2a1 1 0 0 1-.86 1.5H3.56a1 1 0 0 1-.86-1.5Z"
        fill="currentColor"
      />
      <path
        d="M12 9.4v4.2"
        stroke="var(--popover)"
        strokeWidth="2.3"
        strokeLinecap="round"
      />
      <circle cx="12" cy="16.3" r="1.2" fill="var(--popover)" />
    </svg>
  );
}

function SolidError({ className }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" fill="currentColor" />
      <path
        d="m9 9 6 6M15 9l-6 6"
        stroke="var(--popover)"
        strokeWidth="2.3"
        strokeLinecap="round"
      />
    </svg>
  );
}

const toastIcons = {
  success: <SolidCheck className="size-4" />,
  info: <SolidInfo className="size-4" />,
  warning: <SolidWarning className="size-4" />,
  error: <SolidError className="size-4" />,
  close: <X aria-hidden="true" className="size-4" strokeWidth={2.2} />,
};

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
      gap={10}
      icons={toastIcons}
      toastOptions={{
        classNames: {
          toast: "group toast font-sans",
          content: "toast-content",
          title: "toast-title",
          description: "toast-description",
          icon: "toast-icon",
          loader: "toast-loader",
          actionButton: "toast-action",
          cancelButton: "toast-cancel",
          closeButton: "toast-close",
        },
      }}
      {...props}
    />
  );
}

export { Toaster };
