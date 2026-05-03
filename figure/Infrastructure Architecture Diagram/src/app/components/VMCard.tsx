type BadgeItem =
  | string
  | { label: string; bgColor: string; textColor?: string };

export interface VMCardProps {
  name: string;
  subtitle?: string;
  subtitle2?: string;
  borderColor: string;
  badges?: BadgeItem[];
  failoverText?: string;
}

function renderBadge(badge: BadgeItem, idx: number) {
  if (typeof badge === "string") {
    return (
      <span
        key={idx}
        className="bg-gray-100 text-gray-700 text-xs px-2 py-0.5 rounded"
      >
        {badge}
      </span>
    );
  }
  return (
    <span
      key={idx}
      className="text-xs px-2 py-0.5 rounded"
      style={{
        backgroundColor: badge.bgColor,
        color: badge.textColor ?? "white",
      }}
    >
      {badge.label}
    </span>
  );
}

export function VMCard({
  name,
  subtitle,
  subtitle2,
  borderColor,
  badges,
  failoverText,
}: VMCardProps) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="border-l-4 p-3" style={{ borderLeftColor: borderColor }}>
        <h4 className="font-bold text-gray-900 text-sm">{name}</h4>

        <p
          className={`text-xs mt-0.5 ${
            failoverText ? "text-red-600" : "text-gray-600"
          }`}
        >
          {failoverText ?? subtitle ?? "\u00A0"}
        </p>

        {subtitle2 && (
          <p className="text-xs text-gray-600 mt-0.5">{subtitle2}</p>
        )}

        {badges && badges.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {badges.map((badge, idx) => renderBadge(badge, idx))}
          </div>
        )}
      </div>
    </div>
  );
}
