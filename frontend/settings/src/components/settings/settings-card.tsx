import type { ReactNode } from "react"

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Field, FieldContent, FieldDescription, FieldLabel } from "@/components/ui/field"
import { cn } from "@/lib/utils"

export function SettingsPage({
  title,
  description,
  children,
}: {
  title: string
  description?: string
  children: ReactNode
}) {
  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-4 pb-8">
      <header className="px-0.5 pt-0.5">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description ? (
          <p className="mt-1 text-xs text-muted-foreground">{description}</p>
        ) : null}
      </header>
      {children}
    </section>
  )
}

export function SettingsCard({
  title,
  description,
  children,
  className,
}: {
  title?: string
  description?: string
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={cn("gap-0 py-0 shadow-none", className)}>
      {title || description ? (
        <CardHeader className="gap-1 border-b px-4 py-3">
          {title ? <CardTitle className="text-sm">{title}</CardTitle> : null}
          {description ? (
            <CardDescription className="text-xs leading-relaxed">
              {description}
            </CardDescription>
          ) : null}
        </CardHeader>
      ) : null}
      <CardContent className="p-0">{children}</CardContent>
    </Card>
  )
}

export function SettingsRow({
  label,
  description,
  htmlFor,
  children,
  className,
}: {
  label: string
  description?: string
  htmlFor?: string
  children: ReactNode
  className?: string
}) {
  return (
    <Field
      orientation="horizontal"
      className={cn(
        "min-h-14 items-center border-b px-4 py-3 last:border-b-0 max-[600px]:flex-col max-[600px]:items-stretch",
        className,
      )}
    >
      <FieldContent>
        <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>
        {description ? (
          <FieldDescription className="max-w-lg text-xs">
            {description}
          </FieldDescription>
        ) : null}
      </FieldContent>
      <div className="flex shrink-0 items-center gap-2 max-[600px]:w-full max-[600px]:justify-end">
        {children}
      </div>
    </Field>
  )
}

export function InlineValue({ children }: { children: ReactNode }) {
  return (
    <span className="text-xs font-medium text-muted-foreground">
      {children}
    </span>
  )
}
