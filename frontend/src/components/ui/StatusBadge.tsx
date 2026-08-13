interface StatusBadgeProps {
  label: string;
  tone?: "blue" | "green" | "amber" | "red" | "gray";
}

export function StatusBadge({ label, tone = "gray" }: StatusBadgeProps) {
  return <span className={`statusBadge statusBadge-${tone}`}>{label.replace(/_/g, " ")}</span>;
}
