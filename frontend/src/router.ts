import { createRouter, createWebHistory } from 'vue-router'

import { auth, loadAuth } from './auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** Needs a signed-in user (default). */
    public?: boolean
    admin?: boolean
    title?: string
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/setup',
      name: 'setup',
      component: () => import('./views/SetupView.vue'),
      meta: { public: true, title: 'Set up' },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('./views/LoginView.vue'),
      meta: { public: true, title: 'Sign in' },
    },
    {
      path: '/new',
      name: 'new',
      component: () => import('./views/NewFuelUpView.vue'),
      meta: { title: 'New fuel-up' },
    },
    {
      path: '/',
      name: 'home',
      component: () => import('./views/HomeView.vue'),
      meta: { title: 'Fuel-ups' },
    },
    {
      path: '/fuel-ups/:id',
      name: 'fuel-up',
      component: () => import('./views/FuelUpView.vue'),
      meta: { title: 'Fuel-up' },
    },
    {
      path: '/receipts/:id',
      name: 'receipt',
      component: () => import('./views/ReceiptView.vue'),
      meta: { title: 'Receipt' },
    },
    {
      path: '/notifications',
      name: 'notifications',
      component: () => import('./views/NotificationsView.vue'),
      meta: { title: 'Notifications' },
    },
    {
      path: '/account',
      name: 'account',
      component: () => import('./views/AccountView.vue'),
      meta: { title: 'Account' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  if (!auth.loaded) await loadAuth()

  if (auth.needsSetup) return to.name === 'setup' ? true : { name: 'setup' }
  if (to.name === 'setup') return { name: 'login' }

  if (!auth.user) {
    if (to.meta.public) return true
    return { name: 'login', query: to.fullPath !== '/' ? { next: to.fullPath } : {} }
  }
  if (to.name === 'login') return { name: 'home' }
  if (to.meta.admin && auth.user.role !== 'admin') return { name: 'home' }
  return true
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · Fuel Tracker` : 'Fuel Tracker'
})
