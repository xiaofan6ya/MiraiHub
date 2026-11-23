import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import LoginPage from '@/login/LoginPage.vue'

const httpMock = vi.fn()
vi.mock('@/api/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/http')>()
  return { ...actual, http: (...args: unknown[]) => httpMock(...args) }
})

describe('登录页', () => {
  beforeEach(() => {
    httpMock.mockReset()
    // onMounted 会拉版本号；默认给一个已解析的响应，避免 mock 返回 undefined。
    httpMock.mockResolvedValue({ version: '1.0.0' })
  })

  it('渲染用户名/密码与登录按钮', () => {
    const wrapper = mount(LoginPage)
    expect(wrapper.find('h1').text()).toContain('MiraiHub')
    expect(wrapper.findAll('input')).toHaveLength(2)
    expect(wrapper.find('button[type=submit]').text()).toContain('登录')
  })

  it('空表单提交提示必填，不发请求', async () => {
    const wrapper = mount(LoginPage)
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.login__error').text()).toContain('请输入用户名和密码')
    // onMounted 的版本号请求不算登录请求：断言没有 POST /api/login。
    expect(httpMock.mock.calls.some((call) => call[1] === '/api/login')).toBe(false)
  })

  it('401 映射为用户名或密码错误并清空密码', async () => {
    const { ApiError } = await import('@/api/http')
    httpMock.mockImplementation((_method: string, path: string) =>
      path === '/api/login'
        ? Promise.reject(new ApiError(401, 'unauthorized'))
        : Promise.resolve({ version: '1.0.0' }),
    )
    const wrapper = mount(LoginPage)
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('admin')
    await inputs[1].setValue('secret')
    await wrapper.find('form').trigger('submit')
    await new Promise((r) => setTimeout(r, 0))
    expect(wrapper.find('.login__error').text()).toContain('用户名或密码错误')
    expect((inputs[1].element as HTMLInputElement).value).toBe('')
  })
})