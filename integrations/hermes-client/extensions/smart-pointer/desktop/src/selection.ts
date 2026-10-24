export interface Point { x: number; y: number }
export interface Region extends Point { width: number; height: number }
export interface ImageSize { width: number; height: number }

/** Pointer coordinates use the painted image, never the surrounding panel. */
export function imagePoint(client: Point, bounds: Region, image: ImageSize): Point {
  if (bounds.width <= 0 || bounds.height <= 0) throw new Error('Image is not ready')
  return { x: Math.max(0, Math.min(image.width, (client.x - bounds.x) * image.width / bounds.width)),
    y: Math.max(0, Math.min(image.height, (client.y - bounds.y) * image.height / bounds.height)) }
}

export function cropRegion(start: Point, end: Point): Region | null {
  const width = Math.round(Math.abs(end.x - start.x)), height = Math.round(Math.abs(end.y - start.y))
  if (width < 4 || height < 4) return null
  return { x: Math.round(Math.min(start.x, end.x)), y: Math.round(Math.min(start.y, end.y)), width, height }
}

export class RevisionGate {
  private value = 0
  next(): number { return ++this.value }
  accepts(revision: number): boolean { return this.value === revision }
}

export interface ComposerScope { sessionId: string; profile: string; connectionId: string | null }
export function sameScope(left: ComposerScope, right: ComposerScope): boolean {
  return left.sessionId === right.sessionId && left.profile === right.profile && left.connectionId === right.connectionId
}
