import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
  tone: "ink" | "mint" | "amber" | "rose";
}

export function StatCard({ detail, icon: Icon, label, tone, value }: StatCardProps) {
  return (
    <article className={`statCard statCard-${tone}`}>
      <div className="statIcon">
        <Icon size={20} />
      </div>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </article>
  );
}
