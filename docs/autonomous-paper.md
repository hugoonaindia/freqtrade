# Perfil autónomo Paper de varios días

El panel incorpora un perfil de hasta el 100% del capital disponible, sin apalancamiento. Solo compra si la estrategia seleccionada produce una señal. No fuerza estar siempre invertido: reparte la asignación entre los símbolos configurados, o concentra en el líder si eliges momentum. Usa acciones enteras y un margen operativo del 1%; puede quedar efectivo sin invertir.

## Activación y campaña

1. Elige estrategia y símbolos y guarda los ajustes. Las seis estrategias son reglas de ejemplo, no un sistema validado ni una promesa de rentabilidad.
2. Evalúa el perfil «Mi perfil» en el laboratorio histórico.
3. La primera activación requiere cuenta Alpaca Paper sin posiciones ni órdenes abiertas. Pulsa GO PAPER para registrar el patrimonio inicial y habilitar el motor.
4. Las señales usan sesiones diarias completas. No se fuerza cierre al terminar la jornada; las posiciones pueden durar varios días.

Al pausar o reiniciar se conserva el capital inicial de campaña. Reiniciar no habilita entradas: pulsa GO PAPER para reanudarlas. La configuración de estrategia y símbolos sí se conserva. No cambies símbolos con posiciones u órdenes abiertas.

## Los dos límites del 2%

- **Por posición:** entrada limitada con orden OTO y stop de mercado GTC adjunto. El stop queda al 98% del límite de compra, redondeado hacia arriba al incremento permitido. Si la entrada se ejecuta mejor que el límite, la protección puede ser más estrecha que el 2% del precio efectivo. El stop se activa al completarse la entrada. Una entrada parcial provoca cancelación del resto y reconciliación antes de nuevas compras.
- **Sobre la cuenta:** patrimonio igual o inferior al 98% del registrado al activar dispara un bloqueo persistente. Se cancelan compras pendientes, se confirman las cancelaciones de salidas y se solicita la liquidación de posiciones largas durante sesión regular. El bloqueo no se borra aunque la cuenta se recupere o reinicies.

El supervisor comprueba la cuenta cada 30 segundos, también con las entradas en pausa. Solo funciona mientras el proceso esté abierto, con red y equipo despierto. Los stops alojados en Alpaca permanecen allí cuando cierras la aplicación. Las órdenes stop no garantizan un precio de ejecución ni protegen fuera de la sesión regular; gaps, suspensión de cotización, retrasos o falta de conexión pueden superar la pérdida del 2%. La cancelación y liquidación requiere varios ciclos y confirmaciones del bróker.

El límite global observa toda la cuenta, incluidas posiciones o movimientos externos; no ingreses ni retires dinero ni operes por otra vía durante la campaña. Posiciones incompatibles, datos antiguos, protecciones insuficientes o errores de reconciliación bloquean nuevas entradas. Una escritura con respuesta ambigua no se reenvía automáticamente: revisa Alpaca.

Después de revisar una campaña parada, «Archivar campaña tras revisión» permite comenzar otra únicamente con cuenta y órdenes vacías. Conserva el archivo anterior en `.equities-state`. Las claves siguen en `.secrets`, fuera de Git.

## Evaluación histórica

El perfil swing simula capital disponible, stops individuales y bloqueo global. Es una aproximación con OHLC diario: no dispone de cotizaciones históricas ni puede reproducir las cancelaciones de entradas a los 120 segundos, spreads o ejecuciones parciales. Aproxima el límite con el cierre anterior. Los stops se prueban con el mínimo diario y con precio de apertura cuando hay gaps. El límite global se comprueba en apertura y cierre; una activación al cierre se liquida en la siguiente apertura disponible. Las referencias no usan stops. Lee las hipótesis del informe antes de comparar resultados.

Documentación del bróker: https://docs.alpaca.markets/us/docs/orders-at-alpaca
