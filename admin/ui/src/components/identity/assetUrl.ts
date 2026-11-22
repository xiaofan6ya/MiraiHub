/** public/logos/ 下的静态资源地址（base 为构建时的 /admin/v2/）。 */
export function logoUrl(file: string): string {
  const base = String(import.meta.env.BASE_URL || '/').replace(/\/+$/, '')
  return base + '/logos/' + file
}
