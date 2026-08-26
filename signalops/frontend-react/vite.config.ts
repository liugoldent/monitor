import react from '@vitejs/plugin-react'
import { copyFileSync, createReadStream, mkdirSync, statSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'

const projectRoot = dirname(fileURLToPath(import.meta.url))
const csvSourcePath = resolve(projectRoot, '../../backend-futures-py/tv_doc/webhook_data_1min.csv')
const csvPublicPath = '/data/webhook_data_1min.csv'

function localFuturesCsvPlugin(): Plugin {
  return {
    name: 'local-futures-csv',
    configureServer(server) {
      server.middlewares.use(csvPublicPath, (request, response, next) => {
        if (request.method !== 'GET' && request.method !== 'HEAD') {
          next()
          return
        }

        try {
          const stats = statSync(csvSourcePath)
          response.statusCode = 200
          response.setHeader('Content-Type', 'text/csv; charset=utf-8')
          response.setHeader('Content-Length', stats.size)
          response.setHeader('Cache-Control', 'no-store')

          if (request.method === 'HEAD') {
            response.end()
            return
          }

          createReadStream(csvSourcePath).pipe(response)
        } catch {
          response.statusCode = 404
          response.end(`CSV source not found: ${csvSourcePath}`)
        }
      })
    },
    closeBundle() {
      const outputDirectory = resolve(projectRoot, 'dist/data')
      mkdirSync(outputDirectory, { recursive: true })
      copyFileSync(csvSourcePath, resolve(outputDirectory, 'webhook_data_1min.csv'))
    },
  }
}

export default defineConfig({
  plugins: [react(), localFuturesCsvPlugin()],
  server: {
    port: 5373,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
