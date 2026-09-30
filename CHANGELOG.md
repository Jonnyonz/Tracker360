# Notas de los parches

Cambios de Tracker360, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## 2026-09-30

### Corregido
- **Traspasos: ya no se puede transferir más de lo que dice la orden.** El escaneo se rechaza con
  el aviso "Solo faltan N", igual que en el picking. (`c2807e2`)
- **Traspasos: ya no se puede sacar del origen más stock del que hay.** Antes, con 2 unidades en
  la ubicación de origen se podían transferir 5 y el origen quedaba en -3. Ahora se rechaza
  mostrando lo disponible, salvo que esté activada la opción de permitir stock negativo. (`60b6b7c`)
- **Picking: ya no se puede recolectar más de lo que pide el pedido.** Antes, si faltaban 2
  unidades y se escaneaban 50, el sistema las aceptaba: la línea quedaba en 58 de 10 y se
  descontaban 50 del stock. Ahora el escaneo se rechaza con el aviso "Solo faltan N" y no se
  toca ni el pedido ni el stock. Vale para el picking por pedido y por olas. (`2385e63`)
- **Picking: se rechazan las cantidades en cero, negativas o inválidas.** Una cantidad negativa
  restaba de lo recolectado y sumaba stock que no existía. (`2385e63`)
- **Recepción, traspasos, pedidos, conteos y devoluciones: se rechazan las cantidades negativas o
  inválidas.** Antes se aceptaban y desordenaban el stock: un traspaso con una cantidad inválida
  podía dejar el stock de un artículo ilegible y el traspaso como terminado; uno con cantidad
  negativa devolvía mercadería al origen; un conteo podía guardar -1 y el ajuste lo aplicaba al
  stock; se podían crear pedidos con cantidades negativas. En conteos contar 0 sigue siendo
  válido, y en devoluciones un artículo en 0 se sigue tomando como "no devuelto". (`9399036`)
