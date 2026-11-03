type Props = { size?: number; gradient?: boolean };

export function LogoIcon({ size = 22, gradient = false }: Props) {
  const id = `lg-${size}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {gradient && (
        <defs>
          <linearGradient id={id} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#22d3ee" />
            <stop offset="50%" stopColor="#6366f1" />
            <stop offset="100%" stopColor="#34d399" />
          </linearGradient>
        </defs>
      )}
      {/* Minimalist Compass / Guiding Star */}
      <polygon 
        points="12,2 14.5,9.5 22,12 14.5,14.5 12,22 9.5,14.5 2,12 9.5,9.5" 
        fill="none" 
        stroke={gradient ? `url(#${id})` : "currentColor"} 
        strokeWidth="2" 
        strokeLinejoin="round" 
      />
    </svg>
  );
}
