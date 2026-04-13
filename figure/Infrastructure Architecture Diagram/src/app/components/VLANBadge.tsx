interface VLANBadgeProps {
  vlan: number;
  color: string;
}

export function VLANBadge({ vlan, color }: VLANBadgeProps) {
  return (
    <div
      className="rounded px-2 py-0.5 text-white text-xs"
      style={{ backgroundColor: color }}
    >
      {vlan}
    </div>
  );
}
