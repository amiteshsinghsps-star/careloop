import type { Routes } from '@angular/router';
import { authGuard } from './auth.guard';

export const ROUTES: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'plan' },
  { path: 'login', loadComponent: () => import('./pages/login.component').then((m) => m.LoginComponent) },
  {
    path: 'plan',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/plan.component').then((m) => m.PlanComponent),
  },
  { path: '**', loadComponent: () => import('./pages/not-found.component').then((m) => m.NotFoundComponent) },
];
