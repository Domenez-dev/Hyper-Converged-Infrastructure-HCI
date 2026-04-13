export function VMCard({
  name,
  subtitle,
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

        {badges && badges.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {badges.map((badge, idx) => (
              <span
                key={idx}
                className="bg-gray-100 text-gray-700 text-xs px-2 py-0.5 rounded"
              >
                {badge}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
