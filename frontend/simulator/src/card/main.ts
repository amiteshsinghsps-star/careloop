import './card.css';
import Card from './Card.svelte';
import { mount } from 'svelte';

const target = document.getElementById('app');
if (!target) throw new Error('CareLoop card: #app container is missing from card.html');

const app = mount(Card, { target });

export default app;
