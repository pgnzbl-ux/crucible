export type CrucibleLogoVariant = 'mark' | 'lockup' | 'mono' | 'dark'

const SRC: Record<CrucibleLogoVariant, string> = {
  mark: '/logo.svg',
  lockup: '/logo-lockup.svg',
  mono: '/logo-mono.svg',
  dark: '/logo-dark.svg',
}

interface CrucibleLogoProps {
  variant?: CrucibleLogoVariant
  size?: number
  className?: string
  alt?: string
}

export function CrucibleLogo({
  variant = 'mark',
  size = 32,
  className,
  alt = 'Crucible',
}: CrucibleLogoProps) {
  const isLockup = variant === 'lockup'

  return (
    <img
      src={SRC[variant]}
      alt={alt}
      width={isLockup ? undefined : size}
      height={size}
      className={className}
      style={isLockup ? { height: size, width: 'auto' } : undefined}
      draggable={false}
    />
  )
}
