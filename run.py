from app import create_app

# ProxyFix (X-Forwarded-For/Proto/Host from Traefik) is applied in create_app().
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
