export function canMutateLab(liveTaskCount: number): boolean {
  return liveTaskCount === 0
}
