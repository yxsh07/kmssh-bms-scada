import Fastify from 'fastify';
import { Asset, Point } from 'shared';

const fastify = Fastify({ logger: true });

fastify.get('/', async (request, reply) => {
  const samplePoint: Point = {
    id: 'p-1',
    name: 'Temperature Sensor',
    value: 23.5,
    timestamp: new Date().toISOString(),
    unit: 'C'
  };

  const sampleAsset: Asset = {
    id: 'a-1',
    name: 'Chiller 1',
    points: [samplePoint],
    status: 'active'
  };

  return { hello: 'world', asset: sampleAsset };
});

const start = async () => {
  try {
    await fastify.listen({ port: 3000 });
  } catch (err) {
    fastify.log.error(err);
    process.exit(1);
  }
};

start();
