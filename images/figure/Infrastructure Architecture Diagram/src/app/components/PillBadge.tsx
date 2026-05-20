interface PillBadgeProps {
  label: string;
  color: string;
}

export function PillBadge({ label, color }: PillBadgeProps) {
  return (
    <div
      className="flex items-center gap-2 rounded-full px-3 py-1.5"
      style={{ backgroundColor: color }}
    >
      <div className="w-2 h-2 rounded-full bg-white"></div>
      <span className="text-white text-sm">{label}</span>
    </div>
  );
}
